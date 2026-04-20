#!/usr/bin/env bash
# Dreamwalker — optional watchdog.
#
# Purpose: keep Claude Desktop running. Not required for the bridge to work —
# install only if you've had Claude crash on you and want an auto-restart.
#
# Behaviour:
#   • check every 5 min
#   • if Claude Desktop is not running, start it
#   • rate-limit: at most 3 restarts per hour
#   • quiet logging (only logs when state changes)

set -u

DREAM_HOME="${HOME}/.dreamwalker"
LOG="${DREAM_HOME}/logs/watchdog.log"
STATE="${DREAM_HOME}/state/watchdog.restarts"
LAST_STATE="${DREAM_HOME}/state/watchdog.last"
BEAT="${DREAM_HOME}/state/watchdog.heartbeat"

APP_NAME="Claude"
CHECK_SECS=300
MAX_PER_HOUR=3

mkdir -p "$(dirname "${LOG}")" "$(dirname "${STATE}")"

log() {
  printf "%s  %s\n" "$(date '+%Y-%m-%dT%H:%M:%S')" "$*" >> "${LOG}"
}

log_change() {
  local new="$1" msg="$2" old=""
  [ -f "${LAST_STATE}" ] && old="$(cat "${LAST_STATE}")"
  if [ "${new}" != "${old}" ]; then
    log "${msg}"
    echo "${new}" > "${LAST_STATE}"
  fi
}

is_running() {
  pgrep -xq "${APP_NAME}"
}

rate_limit_ok() {
  local now cutoff count
  now=$(date +%s)
  cutoff=$(( now - 3600 ))
  touch "${STATE}"
  awk -v c="${cutoff}" '$1 >= c' "${STATE}" > "${STATE}.tmp" && mv "${STATE}.tmp" "${STATE}"
  count=$(wc -l < "${STATE}" | tr -d ' ')
  if [ "${count}" -ge "${MAX_PER_HOUR}" ]; then
    return 1
  fi
  echo "${now}" >> "${STATE}"
  return 0
}

restart() {
  log "restart attempt: open -a ${APP_NAME}"
  open -a "${APP_NAME}" >> "${LOG}" 2>&1 || log "open -a ${APP_NAME} failed"
}

cycle() {
  touch "${BEAT}"
  if is_running; then
    log_change "ok" "${APP_NAME} running"
    return
  fi
  log_change "down" "${APP_NAME} not running"
  if rate_limit_ok; then
    restart
  else
    log_change "backoff" "too many restarts this hour — backing off"
  fi
}

case "${1:-}" in
  --daemon)
    log "watchdog daemon start (pid $$)"
    while true; do
      cycle
      sleep "${CHECK_SECS}"
    done
    ;;
  --status)
    if is_running; then echo "claude: running"; else echo "claude: down"; fi
    if [ -f "${BEAT}" ]; then
      ts=$(stat -f %m "${BEAT}" 2>/dev/null || echo 0)
      echo "watchdog last beat: $(( $(date +%s) - ts ))s ago"
    else
      echo "watchdog: never started"
    fi
    ;;
  --kick)
    log "manual kick"
    restart
    ;;
  *)
    cat <<USAGE
usage: $0 --daemon | --status | --kick

  --daemon   run forever, check every ${CHECK_SECS}s (used by LaunchDaemon)
  --status   print current state and exit
  --kick     force a restart attempt now
USAGE
    exit 2
    ;;
esac
