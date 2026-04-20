#!/usr/bin/env python3
"""
Dreamwalker — bridge between Cowork's scheduled tasks and macOS pmset.

One file does everything:
  * a small cron evaluator (standard 5 fields — all Cowork ever emits)
  * a sync pass that reads scheduled-tasks.json, computes fire times,
    and reprograms pmset wakes
  * a --summary mode for the friendly post-install recap

Usage:
  sync.py              # run one sync pass (used by the LaunchAgent)
  sync.py --dry-run    # show the plan, change nothing
  sync.py --verbose    # print the plan to stdout in addition to logging
  sync.py --summary    # print the human-friendly recap
  sync.py --summary --plain   # same, no ANSI colors (for logs)

Idempotent. No third-party dependencies.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path


# ============================================================ paths / tuning

HOME = Path.home()
DREAM_HOME = HOME / ".dreamwalker"
STATE_DIR = DREAM_HOME / "state"
LOG_FILE = DREAM_HOME / "logs" / "sync.log"
STATE_FILE = Path(os.environ.get(
    "DREAMWALKER_STATE",
    str(STATE_DIR / "managed-wakes.json"),
))

TASKS_GLOB = os.environ.get(
    "DREAMWALKER_TASKS_GLOB",
    str(HOME / "Library" / "Application Support" / "Claude"
        / "local-agent-mode-sessions" / "*" / "*" / "scheduled-tasks.json"),
)

# Horizon: how far ahead we program wakes. 7 days covers the common cadences
# (daily, weekly) which is 99% of what Cowork users actually schedule. Shorter
# horizon keeps the pmset queue light and avoids the ~50 events ceiling.
# Monthly tasks still get picked up — just only once their fire time falls
# within the next week, which the next LaunchAgent tick catches automatically.
HORIZON_HOURS = 168
# Safety cap to avoid flooding pmset. macOS comfortably accepts ~50; we stay
# well below. The minute-level de-dup in compute_desired_wakes keeps us tight.
MAX_WAKES = 50
# How early we wake up before the task's actual fire time. 120 s gives macOS
# time to come out of deep sleep, re-connect wifi, and let Cowork dispatch.
WAKE_LEAD_SECONDS = 120


# ============================================================ cron evaluator
#
# Minimal POSIX 5-field cron: minute hour day_of_month month day_of_week.
# Supports *, single, a-b, a,b,c, */n, a-b/n. POSIX DOM/DOW OR-combining.
# Sunday = 0 or 7. Not supported: names, @aliases, L/W/#. Cowork never emits
# any of those, so we don't carry the complexity.

_FIELD_BOUNDS = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]


def _parse_field(field: str, lo: int, hi: int) -> tuple[set[int], bool]:
    if field == "*":
        return set(range(lo, hi + 1)), True
    values: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            rng, step_str = part.split("/", 1)
            step = int(step_str)
        else:
            rng = part
        if rng in ("*", ""):
            start, end = lo, hi
        elif "-" in rng:
            a, b = rng.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(rng)
        for v in range(start, end + 1, step):
            if lo <= v <= hi:
                values.add(v)
    return values, False


def _parse_cron(expr: str) -> dict:
    fields = expr.strip().split()
    if len(fields) != 5:
        raise ValueError(f"expected 5 cron fields, got {len(fields)}: {expr!r}")
    parsed: dict = {}
    for name, raw, (lo, hi) in zip(
            ("minute", "hour", "dom", "month", "dow"), fields, _FIELD_BOUNDS):
        vals, star = _parse_field(raw, lo, hi)
        parsed[name] = vals
        parsed[name + "_star"] = star
    if 7 in parsed["dow"]:
        parsed["dow"].discard(7)
        parsed["dow"].add(0)
    return parsed


def _dom_dow_match(dt: datetime, p: dict) -> bool:
    dom_ok = dt.day in p["dom"]
    # datetime.isoweekday(): 1=Mon..7=Sun  →  cron: 0=Sun..6=Sat
    dow_ok = (dt.isoweekday() % 7) in p["dow"]
    if p["dom_star"] and p["dow_star"]:
        return True
    if p["dom_star"]:
        return dow_ok
    if p["dow_star"]:
        return dom_ok
    return dom_ok or dow_ok  # POSIX: both specified → OR


def next_fire(expr: str, after: datetime) -> datetime:
    p = _parse_cron(expr)
    dt = (after + timedelta(minutes=1)).replace(second=0, microsecond=0)
    # Up to one year ahead — plenty for any realistic cadence.
    for _ in range(366 * 24 * 60):
        if (dt.minute in p["minute"]
                and dt.hour in p["hour"]
                and dt.month in p["month"]
                and _dom_dow_match(dt, p)):
            return dt
        dt += timedelta(minutes=1)
    raise ValueError(f"no fire time found within a year for {expr!r}")


def next_fires(expr: str, after: datetime, n: int = 5) -> list[datetime]:
    out: list[datetime] = []
    cur = after
    for _ in range(n):
        cur = next_fire(expr, cur)
        out.append(cur)
    return out


# ============================================================ logging

def _log(msg: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{ts}  {msg}\n")


# ============================================================ tasks file

def find_tasks_file() -> Path | None:
    candidates = glob.glob(TASKS_GLOB)
    if not candidates:
        return None
    return Path(max(candidates, key=os.path.getmtime))


def load_tasks(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"cannot parse {path}: {exc}") from exc
    tasks = data.get("scheduledTasks") if isinstance(data, dict) else data
    if not isinstance(tasks, list):
        raise RuntimeError(f"unexpected shape in {path}: no scheduledTasks array")
    return tasks


# ============================================================ planning

def compute_desired_wakes(tasks: list[dict], now: datetime) -> list[dict]:
    horizon = now + timedelta(hours=HORIZON_HOURS)
    out: list[dict] = []
    for t in tasks:
        if not t.get("enabled"):
            continue
        expr = t.get("cronExpression")
        tid = t.get("id", "?")
        if not expr:
            _log(f"skip {tid}: missing cronExpression")
            continue
        try:
            fires = next_fires(expr, now, n=20)
        except Exception as exc:
            _log(f"skip {tid}: cron parse error ({exc})")
            continue
        for f in fires:
            if f > horizon:
                break
            out.append({
                "task_id": tid,
                "fire_at": f,
                "wake_at": f - timedelta(seconds=WAKE_LEAD_SECONDS),
            })
    out.sort(key=lambda x: x["wake_at"])
    # de-duplicate wake times (two tasks at the same minute → one wake is enough)
    seen: set[str] = set()
    unique: list[dict] = []
    for w in out:
        key = w["wake_at"].strftime("%Y-%m-%d %H:%M")
        if key in seen:
            continue
        seen.add(key)
        unique.append(w)
        if len(unique) >= MAX_WAKES:
            break
    return unique


# ============================================================ pmset

def _pmset(args: list[str]) -> subprocess.CompletedProcess:
    cmd = ["sudo", "-n", "/usr/bin/pmset"] + args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=15)


def pmset_cancel_all() -> None:
    r = _pmset(["schedule", "cancelall"])
    if r.returncode != 0:
        _log(f"! cancelall failed: {r.stderr.strip()}")


def pmset_schedule_wake(when: datetime) -> bool:
    stamp = when.strftime("%m/%d/%y %H:%M:%S")
    r = _pmset(["schedule", "wake", stamp])
    if r.returncode != 0:
        _log(f"! schedule wake {stamp} failed: {r.stderr.strip()}")
        return False
    return True


# ============================================================ state

def save_state(wakes: list[dict]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    serializable = [
        {"task_id": w["task_id"],
         "fire_at": w["fire_at"].isoformat(),
         "wake_at": w["wake_at"].isoformat()}
        for w in wakes
    ]
    payload = {
        "synced_at": datetime.now().isoformat(),
        "wakes": serializable,
    }
    STATE_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


# ============================================================ sync

def sync(dry_run: bool = False, verbose: bool = False) -> dict:
    now = datetime.now()
    path = find_tasks_file()
    if path is None:
        msg = "no scheduled-tasks.json found — Cowork not installed?"
        _log(msg)
        if verbose:
            print(msg)
        return {"ok": False, "reason": msg, "wakes": []}

    try:
        tasks = load_tasks(path)
    except Exception as exc:
        _log(f"! {exc}")
        return {"ok": False, "reason": str(exc), "wakes": []}

    desired = compute_desired_wakes(tasks, now)
    if verbose:
        print(f"[sync] source: {path}")
        print(f"[sync] tasks enabled: {sum(1 for t in tasks if t.get('enabled'))}")
        print(f"[sync] wakes planned ({len(desired)}):")
        for w in desired:
            print(f"  wake {w['wake_at']:%Y-%m-%d %H:%M}  "
                  f"→ task {w['task_id']} at {w['fire_at']:%H:%M}")

    if dry_run:
        return {"ok": True, "dry_run": True, "wakes": desired}

    pmset_cancel_all()
    programmed = 0
    for w in desired:
        if pmset_schedule_wake(w["wake_at"]):
            programmed += 1

    save_state(desired)
    _log(f"sync ok — {programmed}/{len(desired)} wakes programmed from {path.name}")
    return {"ok": True, "wakes": desired, "programmed": programmed}


# ============================================================ summary mode
#
# Human-friendly recap: N recurring tasks picked up (with cadence),
# M wakes programmed, the next wake time. Used by install.sh at the end
# of the installation flow; also callable standalone.

DOW_NAMES = {"0": "Sun", "1": "Mon", "2": "Tue", "3": "Wed",
             "4": "Thu", "5": "Fri", "6": "Sat"}


def humanize_cron(expr: str) -> str:
    parts = expr.split()
    if len(parts) != 5:
        return expr
    m, h, dom, mon, dow = parts
    try:
        time_str = f"{int(h):02d}:{int(m):02d}"
    except ValueError:
        return expr
    if dom == "*" and mon == "*" and dow == "*":
        return f"every day at {time_str}"
    if dom == "*" and mon == "*" and dow == "1-5":
        return f"weekdays at {time_str}"
    if dom == "*" and mon == "*" and dow == "0,6":
        return f"weekends at {time_str}"
    if dom == "*" and mon == "*" and "," in dow:
        days = ", ".join(DOW_NAMES.get(d, d) for d in dow.split(","))
        return f"{days} at {time_str}"
    if dom == "*" and mon == "*" and dow in DOW_NAMES:
        return f"every {DOW_NAMES[dow]} at {time_str}"
    if dom.isdigit() and mon == "*" and dow == "*":
        return f"day {dom} of each month at {time_str}"
    if mon == "*" and dow.isdigit() and ("," in dom or "-" in dom):
        return f"{DOW_NAMES.get(dow, dow)}, selected weeks at {time_str}"
    return expr


def _summary_palette(use_color: bool) -> dict:
    def wrap(code: str):
        def f(s: str) -> str:
            return f"\033[{code}m{s}\033[0m" if use_color else s
        return f
    return {"bold": wrap("1"), "dim": wrap("2"),
            "green": wrap("32"), "cyan": wrap("36")}


def print_summary(plain: bool = False) -> int:
    use_color = sys.stdout.isatty() and not plain
    c = _summary_palette(use_color)

    if not STATE_FILE.exists():
        print("No sync state yet. Run sync.py first.")
        return 1
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Cannot read state: {exc}")
        return 1

    wakes = state.get("wakes", [])
    if not wakes:
        print("No wakes programmed. Is any Cowork task enabled?")
        return 0

    # Ground truth for task list: Cowork's scheduled-tasks.json, not
    # managed-wakes (which is de-duplicated and may hide colliding tasks).
    enabled_tasks: list[dict] = []
    tf = find_tasks_file()
    if tf is not None:
        try:
            data = json.loads(tf.read_text(encoding="utf-8"))
            enabled_tasks = [t for t in data.get("scheduledTasks", []) if t.get("enabled")]
        except Exception:
            pass

    # Per-task fire counts over 7 days (independent of the dedup cap).
    now = datetime.now()
    horizon = now + timedelta(days=7)
    fires_per_task: dict[str, int] = {}
    for t in enabled_tasks:
        try:
            fires = [f for f in next_fires(t["cronExpression"], now, n=50) if f <= horizon]
            fires_per_task[t["id"]] = len(fires)
        except Exception:
            fires_per_task[t["id"]] = 0

    tasks_with_own_wake = {w["task_id"] for w in wakes}

    wakes_sorted = sorted(wakes, key=lambda w: w["wake_at"])
    next_w = wakes_sorted[0]
    next_dt = datetime.fromisoformat(next_w["wake_at"])
    next_fire_dt = datetime.fromisoformat(next_w["fire_at"])

    print()
    print(c["bold"]("📋  Detected schedule"))
    print(f"   {c['green'](str(len(enabled_tasks)))} recurring tasks picked up from Cowork:")
    pad = max((len(t["id"]) for t in enabled_tasks), default=20) + 2
    for t in sorted(enabled_tasks, key=lambda t: t["id"]):
        tid = t["id"]
        human = humanize_cron(t.get("cronExpression", "?"))
        fires = fires_per_task.get(tid, 0)
        word = "fire" if fires == 1 else "fires"
        label = tid.ljust(pad)
        share_note = "" if tid in tasks_with_own_wake else c["dim"]("  (shares wake)")
        count = c["dim"](f"({fires} {word} / 7 d)")
        print(f"     • {c['cyan'](label)}{c['dim'](human)}  {count}{share_note}")

    print()
    print(c["bold"](f"🌙  {len(wakes)} wakes programmed for the next 7 days"))
    next_line = f"→ {next_w['task_id']} fires at {next_fire_dt:%H:%M}"
    print(f"   Next wake:  {next_dt:%a %b %d %H:%M}  {c['dim'](next_line)}")
    print()
    print(f"   {c['green']('✓')}  Your Mac will wake up in time, even if asleep.")
    print(f"   {c['green']('✓')}  Schedule auto-refreshes every 10 minutes.")
    print()
    return 0


# ============================================================ main

def main() -> int:
    p = argparse.ArgumentParser(description="Dreamwalker — sync tasks with pmset.")
    p.add_argument("--dry-run", action="store_true",
                   help="show the plan, change nothing")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="print the plan in addition to logging")
    p.add_argument("--summary", action="store_true",
                   help="print the friendly recap instead of running a sync")
    p.add_argument("--plain", action="store_true",
                   help="force plain output (no ANSI colors) — useful in logs")
    args = p.parse_args()

    if args.summary:
        return print_summary(plain=args.plain)

    result = sync(dry_run=args.dry_run, verbose=args.verbose or args.dry_run)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
