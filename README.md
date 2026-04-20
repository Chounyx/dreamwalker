# Dreamwalker

**Make your Cowork scheduled tasks fire even when your Mac is asleep.**

Cowork can already schedule tasks. macOS can already wake itself up. Dreamwalker is the two-hundred-line bridge that sits between them — so your 8 a.m. prospecting report, your Friday review, your daily standup summary all actually run on time, whether or not you happened to be in front of the screen.

## What it does

1. Reads the `scheduled-tasks.json` Cowork writes for you.
2. Computes the next fire times with a tiny built-in cron evaluator.
3. Calls `pmset schedule wake` so your Mac wakes two minutes before each task.
4. Repeats every ten minutes, so new tasks are picked up automatically.

That's the whole product. No dashboard to monitor, no configuration to tweak, no daemon to keep an eye on.

## What it does not do

- It does not create, edit, or manage tasks — that is Cowork's job.
- It does not wake a Mac that is fully powered off. `pmset schedule wake` only revives machines from sleep. If you want to shut down and still run tasks, that is a different product.
- It does not route models, track token spend, or replay missed runs. The first version tried to; it was too much for something that should just be plumbing.

## Install

```bash
git clone https://github.com/your-org/dreamwalker.git
cd dreamwalker
./skills/dreamwalker/scripts/install.sh
```

One `sudo` prompt during install — to write the narrow sudoers rule allowing `pmset schedule` without a password afterwards. Everything else runs as your user.

Want the optional Claude Desktop watchdog too? Add `--with-watchdog`. It restarts Claude if it crashes, capped at three restarts per hour. Most people never need it.

## Status

```bash
python3 skills/dreamwalker/scripts/sync.py --summary
```

Prints a compact recap in the terminal: every task Cowork has enabled, its cadence, how many times it fires in the next 7 days, and when the very next wake is. If the list is empty and you expected wakes, the output tells you where to look.

## Uninstall

```bash
./skills/dreamwalker/scripts/uninstall.sh
```

Removes the LaunchAgent, the sudoers rule, the optional watchdog, and cancels any pending `pmset` wakes. Runtime logs under `~/.dreamwalker/` are left alone so you can read them one last time if you want to; delete the folder yourself when you're done.

## How it works, in one diagram

```
  Cowork scheduled-tasks.json          macOS pmset
        │                                  ▲
        │                                  │
        ▼                                  │
    sync.py  ─────► compute next fires ───►│ program wakes
        ▲                                  │
        │                                  │
   LaunchAgent (every 10 min)              │
```

Three things total: `sync.py` (cron evaluator + sync + recap in one file), a LaunchAgent plist, a sudoers rule. No external dependencies — pure Python standard library.

## Troubleshooting

- **"My task didn't fire overnight."** Check `~/.dreamwalker/logs/sync.log`. The most recent line should say `sync ok — N/M wakes programmed`. If nothing was programmed, the task is disabled in Cowork, or its `cronExpression` is malformed.
- **"`pmset -g sched` shows nothing."** The sudoers rule is missing — re-run the installer.
- **"It worked yesterday, not today."** Your Mac was powered off, not asleep. Sleep-from-lid-close works; shutdown does not.

## License

MIT.
