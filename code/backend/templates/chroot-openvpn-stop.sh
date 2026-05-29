#!/bin/bash
set -euo pipefail
CHROOT="/var/sec/chroot-openvpn"
PID="/run/openvpn-client.pid"
if [ -f "${CHROOT}${PID}" ]; then
  kill "$(cat "${CHROOT}${PID}")" 2>/dev/null || true
  rm -f "${CHROOT}${PID}"
fi
