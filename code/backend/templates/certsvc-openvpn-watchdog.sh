#!/bin/bash
set -euo pipefail

CHROOT="/var/sec/chroot-openvpn"
PID_FILE="${CHROOT}/run/openvpn-client.pid"
OPENVPN_LOG="${CHROOT}/var/log/openvpn-client.log"
CLIENT_CONF="${CHROOT}/etc/openvpn/client.conf"
TUN_DEV="tun-utm9"
STARTER="/usr/local/sbin/chroot-openvpn-start.sh"
STOPPER="/usr/local/sbin/chroot-openvpn-stop.sh"
ENSURE_FW="/usr/local/sbin/certsvc-ensure-fw.sh"
STATE_FILE="/tmp/.certsvc-watchdog-state"
LOG_FILE="/tmp/certsvc-openvpn-watchdog.log"
FAIL_THRESHOLD=3
RESTART_COOLDOWN=300
TAIL_LINES=200
SERVER_IP=""
VPN_PORT=""

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE"
}

validate_config() {
  command -v ip >/dev/null 2>&1 || {
    log "[ERROR] missing required command: ip"
    return 1
  }
}

extract_fw_value() {
  local key="$1"
  local script="$2"
  sed -n "s/^${key}=\"\([^\"]*\)\"/\1/p" "$script" | head -1
}

load_fw_runtime_values() {
  if [ ! -f "$ENSURE_FW" ]; then
    return 0
  fi

  SERVER_IP="$(extract_fw_value "SERVER_IP" "$ENSURE_FW" || true)"
  VPN_PORT="$(extract_fw_value "VPN_PORT" "$ENSURE_FW" || true)"
}

ensure_firewall_runtime() {
  if [ -x "$ENSURE_FW" ]; then
    "$ENSURE_FW" --runtime-only || log "[WARN] ensure-fw runtime-only failed"
  else
    log "[WARN] ensure-fw not found or not executable: $ENSURE_FW"
  fi
}

ensure_client_nobind() {
  [ -f "$CLIENT_CONF" ] || {
    log "[WARN] client.conf not found: $CLIENT_CONF"
    return 0
  }

  if ! grep -q '^nobind$' "$CLIENT_CONF"; then
    echo 'nobind' >> "$CLIENT_CONF"
    log "[INFO] added nobind to client.conf"
  fi
}

clear_openvpn_conntrack() {
  if ! command -v conntrack >/dev/null 2>&1; then
    log "[WARN] conntrack command not found, skip cleanup"
    return 0
  fi

  if [ -z "$SERVER_IP" ] || [ -z "$VPN_PORT" ]; then
    log "[WARN] server ip/port unavailable, skip conntrack cleanup"
    return 0
  fi

  conntrack -D -p udp --orig-dst "$SERVER_IP" --dport "$VPN_PORT" 2>/dev/null || true
  conntrack -D -p udp --reply-src "$SERVER_IP" --sport "$VPN_PORT" 2>/dev/null || true
  log "[INFO] cleared stale OpenVPN conntrack entries for ${SERVER_IP}:${VPN_PORT}"
}

load_state() {
  FAIL_COUNT=0
  LAST_RESTART_TS=0

  [ -f "$STATE_FILE" ] || return 0

  while IFS='=' read -r key value; do
    case "$key" in
      FAIL_COUNT)
        [[ "$value" =~ ^[0-9]+$ ]] && FAIL_COUNT="$value"
        ;;
      LAST_RESTART_TS)
        [[ "$value" =~ ^[0-9]+$ ]] && LAST_RESTART_TS="$value"
        ;;
    esac
  done < "$STATE_FILE"
}

save_state() {
  umask 077
  cat > "$STATE_FILE" <<EOF
FAIL_COUNT=${FAIL_COUNT}
LAST_RESTART_TS=${LAST_RESTART_TS}
EOF
}

pid_is_alive() {
  local pid

  [ -f "$PID_FILE" ] || return 1
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

tun_is_healthy() {
  ip link show dev "$TUN_DEV" >/dev/null 2>&1 || return 1
  ip addr show dev "$TUN_DEV" 2>/dev/null | grep -q 'inet '
}

log_tail_matches() {
  local pattern="$1"

  [ -f "$OPENVPN_LOG" ] || return 1
  tail -n "$TAIL_LINES" "$OPENVPN_LOG" 2>/dev/null | grep -Eq "$pattern"
}

record_restart() {
  FAIL_COUNT=0
  LAST_RESTART_TS="$(date +%s)"
  save_state
}

start_openvpn() {
  ensure_firewall_runtime
  ensure_client_nobind
  "$STARTER"
  record_restart
}

restart_openvpn() {
  local reason="$1"
  local now

  now="$(date +%s)"
  if (( now - LAST_RESTART_TS < RESTART_COOLDOWN )); then
    log "[WARN] unhealthy (${reason}) but restart skipped due to cooldown"
    return 0
  fi

  log "[WARN] unhealthy (${reason}); restarting OpenVPN"
  ensure_firewall_runtime
  clear_openvpn_conntrack
  "$STOPPER" || true
  ensure_client_nobind
  "$STARTER"
  record_restart
}

main() {
  local reason=""
  local tun_healthy=0

  mkdir -p "$(dirname "$LOG_FILE")"
  touch "$LOG_FILE"

  validate_config
  load_state
  load_fw_runtime_values
  ensure_firewall_runtime

  if ! pid_is_alive; then
    log "[WARN] OpenVPN client process is not running; starting"
    start_openvpn
    exit 0
  fi

  if tun_is_healthy; then
    tun_healthy=1
  fi

  if [ "$tun_healthy" -eq 0 ]; then
    reason="tun interface missing or has no IPv4 address"
  fi

  if log_tail_matches 'write UDPv4: Operation not permitted'; then
    if [ -n "$reason" ]; then
      reason="${reason}; UDP write blocked by local firewall"
    else
      reason="UDP write blocked by local firewall"
    fi
  fi

  if log_tail_matches 'TLS Error|Inactivity timeout|ping-restart|Connection reset|Restart pause|SIGUSR1\[soft,'; then
    if [ -n "$reason" ]; then
      reason="${reason}; reconnect loop detected"
    else
      reason="reconnect loop detected"
    fi
  elif [ "$tun_healthy" -eq 0 ] && log_tail_matches 'Initialization Sequence Completed'; then
    reason="${reason}; tunnel disappeared after successful init"
  fi

  if [ -z "$reason" ]; then
    if [ "$FAIL_COUNT" -ne 0 ]; then
      log "[INFO] tunnel healthy again; resetting failure counter"
      FAIL_COUNT=0
      save_state
    fi
    exit 0
  fi

  FAIL_COUNT=$((FAIL_COUNT + 1))
  save_state
  log "[WARN] ${reason}; failure ${FAIL_COUNT}/${FAIL_THRESHOLD}"

  if [ "$FAIL_COUNT" -lt "$FAIL_THRESHOLD" ]; then
    exit 0
  fi

  restart_openvpn "$reason"
}

main "$@"