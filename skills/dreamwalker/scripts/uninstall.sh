#!/usr/bin/env bash
# Dreamwalker — uninstaller. Removes everything the installer put in place.

set -u

AGENT_LABEL="com.dreamwalker.sync"
AGENT_PLIST="${HOME}/Library/LaunchAgents/${AGENT_LABEL}.plist"

DAEMON_LABEL="com.dreamwalker.watchdog"
DAEMON_PLIST="/Library/LaunchDaemons/${DAEMON_LABEL}.plist"

SUDOERS_DST="/etc/sudoers.d/dreamwalker"

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
dim()  { printf "\033[2m%s\033[0m\n" "$*"; }
ok()   { printf "\033[32m✓\033[0m %s\n" "$*"; }

bold "Dreamwalker — uninstall"

# agent
if [ -f "${AGENT_PLIST}" ]; then
  launchctl unload "${AGENT_PLIST}" 2>/dev/null || true
  rm -f "${AGENT_PLIST}"
  ok "Removed LaunchAgent"
fi

# watchdog daemon (if present)
if [ -f "${DAEMON_PLIST}" ]; then
  sudo launchctl unload "${DAEMON_PLIST}" 2>/dev/null || true
  sudo rm -f "${DAEMON_PLIST}"
  ok "Removed watchdog daemon"
fi

# sudoers
if [ -f "${SUDOERS_DST}" ]; then
  sudo rm -f "${SUDOERS_DST}"
  ok "Removed sudoers rule"
fi

# cancel any wakes we programmed
sudo -n /usr/bin/pmset schedule cancelall 2>/dev/null || true
ok "Cancelled pmset wakes"

dim "Runtime data kept at ~/.dreamwalker (delete manually if you want it gone)."
