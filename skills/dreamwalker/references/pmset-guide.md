# pmset — operational notes for Dreamwalker

Dreamwalker's whole purpose is to translate "Cowork has a scheduled task" into "macOS will be awake for it". This file documents the exact `pmset` behaviour Dreamwalker depends on, the permissions it needs, and what to check when a wake does not fire.

## The one critical command

```bash
sudo pmset schedule wake "04/20/2026 07:58:00"
```

This is the only macOS command that reliably wakes a sleeping Mac at a precise time, even on battery. The system `powerd` daemon handles it at the kernel level. `cron`, `launchd`, and any userland timer cannot bring a Mac out of deep sleep; `pmset schedule wake` can.

Required timestamp format: `MM/dd/yyyy HH:mm:ss` — local time, no ISO, no timezone suffix.

## Permissions

Every call that modifies scheduled wakes requires root. To keep the bridge quiet, Dreamwalker installs a narrow sudoers rule at install time:

```
# /etc/sudoers.d/dreamwalker
guillaume ALL=(root) NOPASSWD: /usr/bin/pmset schedule *
```

The rule covers only `pmset schedule` — it does not grant any other privilege. Read-only calls like `pmset -g sched` do not need sudo at all.

## Commands Dreamwalker uses

| command                              | purpose                           |
|--------------------------------------|-----------------------------------|
| `sudo pmset schedule wake <ts>`      | program a single wake             |
| `sudo pmset schedule cancelall`      | wipe every scheduled wake         |
| `pmset -g sched`                     | list what is currently scheduled  |

`sync.py` uses only `cancelall` + fresh reprogramming on each run. No diffing, no partial cancels. This keeps the code short and makes it trivial to reason about.

## Known gotchas

**Fully powered-off Macs will not wake.** `pmset schedule wake` only fires while the Mac is asleep (S3) or in standby. If the user shuts the machine down, no wake can revive it. This is a hard OS limit and should be stated plainly.

**"Wake for network access" needs to be enabled** in Settings → Battery/Energy. Otherwise the wake may fire but the screen stays dark and some background apps fail to start cleanly.

**Low battery safeguards can skip a wake.** On an unplugged laptop with battery below roughly 10 %, macOS may silently drop a scheduled wake. There is no reliable way to override this.

**Deep hibernation (S4) ignores scheduled wakes.** Modern MacBooks rarely enter S4 when plugged in. Forcing `standby` instead:

```bash
sudo pmset -a hibernatemode 0
```

Dreamwalker does not change this automatically — it affects battery life.

**Too many wakes can get dropped.** macOS accepts many scheduled events but the practical ceiling sits around 50. Dreamwalker caps itself at 50 (`MAX_WAKES` in `sync.py`) and de-duplicates when two tasks share the same minute, which keeps real usage comfortably under the ceiling.

## Debugging a missed wake

1. `pmset -g sched` — is the wake actually scheduled?
2. `pmset -g log | grep -i wake` — did the system attempt a wake?
3. `log show --predicate 'process == "powerd"' --last 24h` — detailed `powerd` logs
4. Check "Wake for network access" in Settings → Battery
5. Verify the Mac was not fully off: `last reboot`

## How Dreamwalker uses these commands

`sync.py` runs every ten minutes via a user LaunchAgent. Each run:

1. Finds the most recent `scheduled-tasks.json` under `~/Library/Application Support/Claude/local-agent-mode-sessions/`.
2. Parses every enabled task's `cronExpression` and computes fire times within the next 7 days using the cron evaluator built into `sync.py`.
3. Subtracts a 120-second lead (so the Mac is actually awake when Cowork fires the task).
4. De-duplicates minute collisions and caps at 50 wakes.
5. Runs `sudo -n pmset schedule cancelall`, then `sudo -n pmset schedule wake <ts>` for each wake.
6. Writes the list to `~/.dreamwalker/state/managed-wakes.json` for the status page.

Because step 5 resets everything each time, Dreamwalker is the sole writer by convention. If another tool also schedules wakes, they will be wiped on the next sync — this is a deliberate trade-off in favour of simplicity.
