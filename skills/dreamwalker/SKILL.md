---
name: dreamwalker
description: Silent bridge that makes Cowork's scheduled tasks fire even when the Mac is asleep. Use this skill when the user asks to install Dreamwalker, check its status, diagnose a missed task, make their Mac wake up for a scheduled task, or uninstall the plugin. Trigger phrases include "dreamwalker", "overnight task", "scheduled task didn't run", "wake my mac", "pmset", "sleep task", "cowork schedule missed", "morning task", or "why didn't my task fire last night".
---

# Dreamwalker

Dreamwalker is a small, invisible bridge between the scheduled tasks you create in Cowork and macOS `pmset`. It reads Cowork's `scheduled-tasks.json`, computes the next fire times with a built-in cron evaluator, and tells the Mac to wake up a couple of minutes before each task. Once installed it runs on its own — the user should never need to think about it.

There is no task authoring here. Tasks are created in Cowork as usual. Dreamwalker only ensures the machine is awake when the cron time arrives.

## When to use this skill

Invoke when the user:

- asks to install or uninstall Dreamwalker
- wants to know if Dreamwalker is working or programmed any wakes
- reports that a scheduled Cowork task did not run overnight or while the Mac was asleep
- asks about the `pmset schedule` configuration tied to Dreamwalker
- wants to enable or disable the optional Claude Desktop watchdog

Do **not** invoke this skill for creating, editing, or disabling scheduled tasks themselves — those operations belong to Cowork's native `scheduled-tasks` tooling. Dreamwalker is read-only on the task list.

## Where things live

Runtime directory: `~/.dreamwalker/`

| path                                     | purpose                                               |
|------------------------------------------|-------------------------------------------------------|
| `~/.dreamwalker/logs/sync.log`           | every sync invocation, newest at bottom               |
| `~/.dreamwalker/logs/watchdog.log`       | only if the optional watchdog is installed            |
| `~/.dreamwalker/state/managed-wakes.json`| last computed wake list (what `pmset` was told)       |

Plugin scripts (read-only, inside the plugin bundle):

| script            | role                                                              |
|-------------------|-------------------------------------------------------------------|
| `scripts/sync.py` | the whole bridge — cron evaluator, sync pass, `--summary` recap   |
| `scripts/install.sh` / `uninstall.sh` | install/remove the bridge                     |
| `scripts/watchdog.sh` | optional — keeps Claude Desktop alive                         |

## Installation

```bash
# from inside the plugin directory
./skills/dreamwalker/scripts/install.sh

# or, with the optional Claude Desktop watchdog
./skills/dreamwalker/scripts/install.sh --with-watchdog
```

The installer:

1. creates `~/.dreamwalker/{logs,state}`
2. writes `/etc/sudoers.d/dreamwalker` so `pmset schedule` runs without a password prompt (this is the only privilege Dreamwalker needs)
3. loads a user LaunchAgent `com.dreamwalker.sync` that runs `sync.py` every 10 minutes
4. runs `sync.py` once so the first wakes are programmed immediately

The sudo prompt appears exactly once during install.

## Checking status

From the terminal:

```bash
python3 scripts/sync.py --summary
```

Prints the enabled tasks Cowork knows about with their cadence, how many times each fires in the next 7 days, the total wakes programmed, and the next wake time. No data is stored on disk beyond `~/.dreamwalker/state/managed-wakes.json`, which is overwritten on every sync.

## Diagnosing a missed task

When a user reports "my task didn't fire":

1. Open `~/.dreamwalker/logs/sync.log` and look for the most recent sync line. It should say `sync ok — N/M wakes programmed`.
2. If the log is empty or stale (>20 min old), the LaunchAgent stopped. Re-run `launchctl load ~/Library/LaunchAgents/com.dreamwalker.sync.plist`.
3. If the sync ran but no wakes were programmed, the task is likely disabled in Cowork, its `cronExpression` is malformed, or Cowork has no `scheduled-tasks.json` yet (it is only created on first use).
4. `sudo pmset -g sched` shows what wakes the OS has actually programmed. If this list is empty while the log claims success, the sudoers rule is not in place — re-run the installer.
5. If the Mac was fully powered off (not sleeping), nothing will have woken it up. `pmset schedule wake` only works from sleep. That is the one hard limit of Dreamwalker and should be stated plainly to the user.

## The optional watchdog

Opt-in only. It restarts Claude Desktop if it crashes, with a cap of three restarts per hour. Most users never need it. If the user wants it:

```bash
./skills/dreamwalker/scripts/install.sh --with-watchdog
```

It checks every five minutes, logs only on state changes, and writes a heartbeat file the user can inspect at `~/.dreamwalker/state/watchdog.heartbeat`.

## Uninstall

```bash
./skills/dreamwalker/scripts/uninstall.sh
```

Unloads both the LaunchAgent and (if present) the watchdog daemon, removes the sudoers rule, and cancels any `pmset` wakes that Dreamwalker had programmed. The user's runtime data under `~/.dreamwalker/` is kept — delete it manually if desired.

## References

- `references/pmset-guide.md` — full notes on how `pmset schedule` behaves, wake reliability, and what macOS versions support what.
