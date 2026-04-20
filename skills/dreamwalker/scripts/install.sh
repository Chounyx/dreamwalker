#!/usr/bin/env bash
# Dreamwalker — minimal installer (bridge only).
#
# What this does:
#   1. creates ~/.dreamwalker/{logs,state}
#   2. installs a tiny sudoers rule so sync.py can call `pmset schedule` without a password
#   3. installs a user LaunchAgent that runs sync.py every 10 minutes
#   4. runs sync.py once to program the first wakes
#
# Optional (with --with-watchdog):
#   5. installs a LaunchDaemon that keeps Claude Desktop alive (opt-in, nice-to-have)
#
# Idempotent: re-running the script just refreshes everything.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DREAM_HOME="${HOME}/.dreamwalker"

AGENT_LABEL="com.dreamwalker.sync"
AGENT_PLIST="${HOME}/Library/LaunchAgents/${AGENT_LABEL}.plist"

DAEMON_LABEL="com.dreamwalker.watchdog"
DAEMON_PLIST="/Library/LaunchDaemons/${DAEMON_LABEL}.plist"

SUDOERS_DST="/etc/sudoers.d/dreamwalker"

WITH_WATCHDOG=0
for arg in "$@"; do
  case "${arg}" in
    --with-watchdog) WITH_WATCHDOG=1 ;;
    -h|--help)
      cat <<USAGE
Dreamwalker installer

Usage:
  install.sh                  install the bridge (recommended)
  install.sh --with-watchdog  also install the optional watchdog daemon
  install.sh --help           show this message
USAGE
      exit 0
      ;;
  esac
done

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
dim()  { printf "\033[2m%s\033[0m\n" "$*"; }
ok()   { printf "\033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "\033[33m!\033[0m %s\n" "$*"; }
fail() { printf "\033[31m✗\033[0m %s\n" "$*" >&2; exit 1; }

PY=""
for candidate in /usr/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if command -v "${candidate}" >/dev/null 2>&1; then PY="${candidate}"; break; fi
done
[ -n "${PY}" ] || fail "python3 not found — install Command Line Tools (xcode-select --install)"

bold "Dreamwalker — install"
dim  "Asks for sudo once (pmset rule + optional watchdog daemon)."
echo

# ---------------------------------------------------------------- 1. runtime
bold "1/4  Runtime directory"
mkdir -p "${DREAM_HOME}/logs" "${DREAM_HOME}/state"
touch "${DREAM_HOME}/logs/sync.log"
ok "Runtime ready at ${DREAM_HOME}"

# ---------------------------------------------------------------- 2. sudoers
bold "2/4  pmset permission (sudoers)"
#
# Figure out the REAL invoking user. whoami() alone is not enough: when the
# script is launched via `osascript ... with administrator privileges`
# (the double-click .command flow), we run as root, and whoami returns
# "root" — which would write a useless sudoers rule for root instead of
# the human user. Fallback chain:
#   1. $SUDO_USER if set and not root (classic sudo case)
#   2. the owner of $HOME (works under osascript admin, where HOME is
#      preserved as the invoking user's home)
#   3. whoami as last resort
if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ]; then
  USER_NAME="${SUDO_USER}"
elif [ "$(id -u)" -eq 0 ] && [ -n "${HOME:-}" ] && [ -d "${HOME}" ]; then
  USER_NAME="$(/usr/bin/stat -f '%Su' "${HOME}")"
else
  USER_NAME="$(whoami)"
fi
if [ "${USER_NAME}" = "root" ] || [ -z "${USER_NAME}" ]; then
  fail "Could not detect a non-root invoking user (got: '${USER_NAME}'). Aborting to avoid a useless sudoers rule."
fi
if [ ! -d "/Users/${USER_NAME}" ]; then
  warn "Home directory /Users/${USER_NAME} not found — continuing but verify the username is correct"
fi
ok "Install user: ${USER_NAME}"
TMP_SUDOERS="$(mktemp)"
cat > "${TMP_SUDOERS}" <<EOF
# Dreamwalker — allow pmset schedule without sudo prompt for ${USER_NAME}
${USER_NAME} ALL=(root) NOPASSWD: /usr/bin/pmset schedule *
EOF
chmod 0440 "${TMP_SUDOERS}"

if sudo visudo -cf "${TMP_SUDOERS}" >/dev/null 2>&1; then
  sudo mv "${TMP_SUDOERS}" "${SUDOERS_DST}"
  sudo chown root:wheel "${SUDOERS_DST}"
  ok "Sudoers rule installed at ${SUDOERS_DST}"
else
  rm -f "${TMP_SUDOERS}"
  fail "Sudoers validation failed — install aborted"
fi

# ---------------------------------------------------------------- 3. agent
bold "3/4  LaunchAgent (sync every 10 min)"
mkdir -p "$(dirname "${AGENT_PLIST}")"
cat > "${AGENT_PLIST}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${AGENT_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PY}</string>
    <string>${SCRIPT_DIR}/sync.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>600</integer>
  <key>StandardOutPath</key>
  <string>${DREAM_HOME}/logs/sync.log</string>
  <key>StandardErrorPath</key>
  <string>${DREAM_HOME}/logs/sync.log</string>
</dict>
</plist>
EOF

launchctl unload "${AGENT_PLIST}" 2>/dev/null || true
launchctl load "${AGENT_PLIST}"
ok "Agent loaded: ${AGENT_LABEL}"

# ---------------------------------------------------------------- 4. first sync
bold "4/4  First sync"
if "${PY}" "${SCRIPT_DIR}/sync.py"; then
  ok "Initial wakes programmed"
else
  warn "First sync reported an issue — check ${DREAM_HOME}/logs/sync.log"
fi

# ------ friendly recap: tell the user *exactly* what we picked up
"${PY}" "${SCRIPT_DIR}/sync.py" --summary || true

# ----------------------------------------------------- optional: watchdog
if [ "${WITH_WATCHDOG}" -eq 1 ]; then
  echo
  bold "Extra — watchdog daemon"
  dim  "Keeps Claude Desktop alive if it crashes. Purely optional."

  WATCHDOG_SH="${SCRIPT_DIR}/watchdog.sh"
  if [ ! -x "${WATCHDOG_SH}" ]; then
    chmod +x "${WATCHDOG_SH}" 2>/dev/null || true
  fi

  TMP_DAEMON="$(mktemp)"
  cat > "${TMP_DAEMON}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${DAEMON_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${WATCHDOG_SH}</string>
    <string>--daemon</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key>
  <string>${DREAM_HOME}/logs/watchdog.log</string>
  <key>StandardErrorPath</key>
  <string>${DREAM_HOME}/logs/watchdog.log</string>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>UserName</key>
  <string>${USER_NAME}</string>
</dict>
</plist>
EOF

  sudo mv "${TMP_DAEMON}" "${DAEMON_PLIST}"
  sudo chown root:wheel "${DAEMON_PLIST}"
  sudo chmod 0644 "${DAEMON_PLIST}"
  sudo launchctl unload "${DAEMON_PLIST}" 2>/dev/null || true
  sudo launchctl load "${DAEMON_PLIST}"
  ok "Watchdog daemon loaded: ${DAEMON_LABEL}"
fi

dim  "Logs: ${DREAM_HOME}/logs/sync.log"
dim  "Uninstall: run scripts/uninstall.sh"
