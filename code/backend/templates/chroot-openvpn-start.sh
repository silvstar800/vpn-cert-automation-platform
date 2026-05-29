#!/bin/bash
set -euo pipefail
CHROOT="/var/sec/chroot-openvpn"
CONF="/etc/openvpn/client.conf"
PID="/run/openvpn-client.pid"
LOG="/var/log/openvpn-client.log"
SERVER_IP="__SERVER_IP__"
VPN_PORT="__VPN_PORT__"
FW_GUARD="__FW_GUARD__"

validate_config() {
  local PLACEHOLDER_SERVER_IP="__SERVER""_IP__"
  local PLACEHOLDER_VPN_PORT="__VPN""_PORT__"
  case "${SERVER_IP}:${VPN_PORT}" in
    *"${PLACEHOLDER_SERVER_IP}"*|*"${PLACEHOLDER_VPN_PORT}"*)
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] unresolved OpenVPN placeholders in chroot-openvpn-start.sh" >&2
      return 1
      ;;
  esac
}

cleanup_stale_openvpn_conntrack() {
  command -v conntrack >/dev/null 2>&1 || return 0

  if ! conntrack -L -p udp 2>/dev/null | grep -qF "$SERVER_IP"; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] no stale OpenVPN conntrack entries detected for ${SERVER_IP}:${VPN_PORT}"
    return 0
  fi

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] cleaning stale OpenVPN conntrack entries for ${SERVER_IP}:${VPN_PORT}"
  conntrack -D -p udp --orig-dst "$SERVER_IP" --dport "$VPN_PORT" 2>/dev/null || true
  conntrack -D -p udp --reply-src "$SERVER_IP" --sport "$VPN_PORT" 2>/dev/null || true
}

mkdir -p "${CHROOT}/run" "${CHROOT}/var/log"
if [ -f "${CHROOT}${PID}" ] && kill -0 "$(cat "${CHROOT}${PID}")" 2>/dev/null; then
  exit 0
fi
validate_config
cleanup_stale_openvpn_conntrack
if [ -x "${FW_GUARD}" ]; then
  if ! "${FW_GUARD}" --full; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WARN] certsvc firewall guard failed: ${FW_GUARD}" >&2
  fi
fi
chroot "$CHROOT" /usr/sbin/openvpn --config "$CONF" --daemon --writepid "$PID" --log-append "$LOG" --verb 6
