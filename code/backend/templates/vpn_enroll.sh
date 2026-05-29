#!/bin/bash
set -Eeuo pipefail

###############################################################################
# vpn_enroll.sh
#
# UTM9 client enrollment script
###############################################################################

SERVER_IP="__SERVER_IP__"
SERVER_API_PORT="__SERVER_API_PORT__"
ENROLL_BASE_URL="__ENROLL_BASE_URL__"
DEFAULT_VPN_PORT="__VPN_PORT__"
LEGACY_VPN_PORT="__LEGACY_VPN_PORT__"
BACKUP_KEY_ID="__BACKUP_KEY_ID__"
VPN_PORT=""
ENROLL_TOKEN="${ENROLL_TOKEN:-}"
ENROLL_CA_CERT="${ENROLL_CA_CERT:-}"
ENROLL_URL="${ENROLL_BASE_URL%/}/enroll"

CHROOT="/var/sec/chroot-openvpn"
OVPN_DIR="${CHROOT}/etc/openvpn"
WORKDIR="/tmp/certsvc-enroll"
STARTER="/usr/local/sbin/chroot-openvpn-start.sh"
STOPPER="/usr/local/sbin/chroot-openvpn-stop.sh"
FW_GUARD="/usr/local/sbin/certsvc-ensure-fw.sh"
WATCHDOG="/usr/local/sbin/certsvc-openvpn-watchdog.sh"
CLEANUP_HELPER="/usr/local/sbin/certsvc-clean-enroll.sh"
IPT_FILE="/var/mdw/etc/iptables/iptable.filter"
IPT_ANCHOR1='-A OUTPUT -m confirmed ! -d 224.0.0.0/4 -j ACCEPT'
IPT_ANCHOR2='-A OUTPUT -o lo -j ACCEPT'

RULE_PERSIST_UDP=""
RULE_PERSIST_TCP=""
RULE_RT_UDP=""
RULE_RT_TCP=""
CURRENT_STEP="startup"
RESP_BODY=""
LOG_FILE="/tmp/certsvc-enroll.log"
AUTO_CLEAN_ON_ERROR="${AUTO_CLEAN_ON_ERROR:-1}"
ENABLE_ERROR_CLEANUP=0
CURL_CACERT_ARGS=""
if [ -n "${ENROLL_CA_CERT}" ] && [ -f "${ENROLL_CA_CERT}" ]; then
  CURL_CACERT_ARGS="--cacert ${ENROLL_CA_CERT}"
else
  CURL_CACERT_ARGS="-k"
fi
mkdir -p "$WORKDIR"
: > "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

fail() {
  log "[ERROR] $*"
  exit 1
}

on_error() {
  local rc=$?
  trap - ERR
  set +e
  log "[ERROR] step=${CURRENT_STEP} line=$1 exit=${rc} command=${BASH_COMMAND}"

  if [ "$AUTO_CLEAN_ON_ERROR" = "1" ] && [ "$ENABLE_ERROR_CLEANUP" = "1" ]; then
    log "[ERROR] auto cleanup enabled; removing partially installed enroll scripts"
    if [ -x "$CLEANUP_HELPER" ]; then
      "$CLEANUP_HELPER" || true
    else
      rm -f "$STARTER" "$STOPPER" "$FW_GUARD" "$WATCHDOG" || true
    fi
  elif [ "$AUTO_CLEAN_ON_ERROR" != "1" ] && [ "$ENABLE_ERROR_CLEANUP" = "1" ]; then
    log "[ERROR] auto cleanup disabled (AUTO_CLEAN_ON_ERROR=${AUTO_CLEAN_ON_ERROR}); leaving installed scripts as-is"
  else
    log "[ERROR] cleanup skipped; install phase not reached"
  fi

  if [ -n "${RESP_BODY:-}" ] && [ -f "${RESP_BODY}" ]; then
    log "[ERROR] Last response body:"
    cat "${RESP_BODY}" || true
  fi
  log "[ERROR] See log file: ${LOG_FILE}"
  exit "$rc"
}
trap 'on_error $LINENO' ERR

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing command: $1"
    exit 1
  }
}

need_cmd iptables
need_cmd openssl
need_cmd tar
need_cmd sed
need_cmd date
need_cmd awk
need_cmd grep
need_cmd tee
need_cmd ip

DOWNLOADER="curl"
if ! command -v curl >/dev/null 2>&1; then
  if command -v wget >/dev/null 2>&1; then
    DOWNLOADER="wget"
  else
    echo "Need curl or wget"
    exit 1
  fi
fi

detect_vpn_port() {
  local version=""
  version="$(openssl version 2>/dev/null | awk '{print $2}')"
  case "$version" in
    0.*|1.0.0*|1.0.1*) printf "%s" "$LEGACY_VPN_PORT" ;;
    *) printf "%s" "$DEFAULT_VPN_PORT" ;;
  esac
}

refresh_rule_vars() {
  RULE_PERSIST_UDP="-A OUTPUT -p udp -d ${SERVER_IP} --dport ${VPN_PORT} -j ACCEPT"
  RULE_PERSIST_TCP="-A OUTPUT -p tcp -d ${SERVER_IP} --dport ${SERVER_API_PORT} -j ACCEPT"
  RULE_RT_UDP="-p udp -d ${SERVER_IP} --dport ${VPN_PORT} -j ACCEPT"
  RULE_RT_TCP="-p tcp -d ${SERVER_IP} --dport ${SERVER_API_PORT} -j ACCEPT"
}

persist_iptables_rules() {
  echo "[*] Persisting iptables rules into ${IPT_FILE} ..."
  if [ ! -f "$IPT_FILE" ]; then
    echo "    - [WARN] ${IPT_FILE} not found, skip persistent insert"
    return 0
  fi

  udp_count="$(awk -v r="$RULE_PERSIST_UDP" '($0==r){c++} END{print c+0}' "$IPT_FILE")"
  tcp_count="$(awk -v r="$RULE_PERSIST_TCP" '($0==r){c++} END{print c+0}' "$IPT_FILE")"
  if [ "$udp_count" -eq 1 ] && [ "$tcp_count" -eq 1 ]; then
    echo "    - [OK] persistent rules already correct"
    return 0
  fi

  file_dir="$(dirname "$IPT_FILE")"
  file_base="$(basename "$IPT_FILE")"
  ts="$(date '+%Y%m%d%H%M%S')"
  backup_file="${file_dir}/${file_base}.bak.${ts}"
  tmp="$(mktemp "${file_dir}/.${file_base}.candidate.XXXXXX")"
  tmp2="$(mktemp "${file_dir}/.${file_base}.final.XXXXXX")"
  anchor="$IPT_ANCHOR1"
  have_anchor="$(awk -v a="$anchor" '($0==a){print "1"; exit}' "$IPT_FILE")"
  if [ "${have_anchor:-0}" != "1" ]; then
    anchor="$IPT_ANCHOR2"
    have_anchor="$(awk -v a="$anchor" '($0==a){print "1"; exit}' "$IPT_FILE")"
  fi

  if [ "${have_anchor:-0}" = "1" ]; then
    awk -v a="$anchor" -v r1="$RULE_PERSIST_UDP" -v r2="$RULE_PERSIST_TCP" '
BEGIN{inserted=0}
{
  if ($0==a && inserted==0) {
    print r1
    print r2
    inserted=1
  }
  print $0
}
END{
  if (inserted==0) {
    print r1
    print r2
  }
}
' "$IPT_FILE" > "$tmp"
  else
    cat "$IPT_FILE" > "$tmp"
    printf "%s\n%s\n" "$RULE_PERSIST_UDP" "$RULE_PERSIST_TCP" >> "$tmp"
  fi

  awk -v r1="$RULE_PERSIST_UDP" -v r2="$RULE_PERSIST_TCP" '
{
  if ($0==r1) {c1++; if (c1>1) next}
  if ($0==r2) {c2++; if (c2>1) next}
  print
}
' "$tmp" > "$tmp2"

  if [ ! -s "$tmp2" ] || ! grep -q '^\*filter' "$tmp2" || ! grep -q '^:OUTPUT ' "$tmp2" || ! grep -q '^COMMIT$' "$tmp2"; then
    echo "    - [ERROR] candidate iptables file validation failed (base structure)"
    rm -f "$tmp" "$tmp2"
    return 1
  fi
  if ! grep -qxF -- "$RULE_PERSIST_UDP" "$tmp2" || ! grep -qxF -- "$RULE_PERSIST_TCP" "$tmp2"; then
    echo "    - [ERROR] candidate iptables file validation failed (required rules)"
    rm -f "$tmp" "$tmp2"
    return 1
  fi

  cp -p "$IPT_FILE" "$backup_file"
  mv -f "$tmp2" "$IPT_FILE"
  rm -f "$tmp"
  echo "    - [OK] persistent rules updated safely (backup: ${backup_file})"
}

ensure_runtime_rule() {
  local chain="$1"
  shift
  if iptables -C "$chain" "$@" 2>/dev/null; then
    return 0
  fi
  if iptables -I "$chain" 1 "$@" 2>/dev/null; then
    return 0
  fi
  echo "    - [WARN] failed to insert runtime rule: $chain $*"
  return 1
}

apply_runtime_rules() {
  echo "[*] Applying runtime iptables rules (top of OUTPUT)..."
  ensure_runtime_rule OUTPUT -p tcp -d "${SERVER_IP}" --dport "${SERVER_API_PORT}" -j ACCEPT || true
  ensure_runtime_rule OUTPUT -p udp -d "${SERVER_IP}" --dport "${VPN_PORT}" -j ACCEPT || true
}

HOST_ARG="${1:-__DEFAULT_HOSTNAME__}"
auto_hostname() {
  local h=""
  if command -v hostname >/dev/null 2>&1; then
    h="$(hostname -f 2>/dev/null || true)"
    [ -n "$h" ] || h="$(hostname 2>/dev/null || true)"
  fi
  h="$(printf "%s" "$h" | tr -d '\r\n' | xargs 2>/dev/null || printf "%s" "$h")"
  printf "%s" "$h"
}

if [ -n "$HOST_ARG" ]; then
  CLIENT_HOSTNAME="$HOST_ARG"
else
  CLIENT_HOSTNAME="$(auto_hostname)"
fi

if [ -z "${CLIENT_HOSTNAME:-}" ]; then
  echo -n "Enter hostname(CN) to enroll: "
  read -r CLIENT_HOSTNAME
fi

[ -n "$CLIENT_HOSTNAME" ] || { echo "hostname is empty"; exit 1; }
echo "$CLIENT_HOSTNAME" | grep -Eq '^[a-zA-Z0-9._-]+$' || {
  echo "Invalid hostname format: $CLIENT_HOSTNAME"
  exit 1
}

echo "[*] Using hostname(CN): $CLIENT_HOSTNAME"

IFNAME="eth1"
CLIENT_MAC="$(cat /sys/class/net/${IFNAME}/address 2>/dev/null | tr '[:upper:]' '[:lower:]' || true)"
if echo "$CLIENT_MAC" | grep -Eq '^([0-9a-f]{2}:){5}[0-9a-f]{2}$'; then
  echo "[*] Using ${IFNAME} MAC: $CLIENT_MAC"
else
  echo "[WARN] Cannot read valid MAC from ${IFNAME} (ignore): ${CLIENT_MAC}"
fi

CLIENT_SERIAL=""
if command -v version >/dev/null 2>&1; then
  VERSION_OUTPUT="$(version 2>/dev/null || true)"
  CLIENT_SERIAL="$(printf '%s\n' "$VERSION_OUTPUT" | awk -F':' '/Serial number/ {gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2; exit}')"
fi

if [ -n "$CLIENT_SERIAL" ]; then
  echo "[*] Using serial number: $CLIENT_SERIAL"
else
  echo "[WARN] Cannot detect serial number from version output"
fi

if [ -z "${ENROLL_TOKEN}" ]; then
  read -r -s -p "Enter enroll secret: " ENROLL_TOKEN
  echo
fi

[ -n "${ENROLL_TOKEN}" ] || { echo "enroll secret is empty"; exit 1; }

VPN_PORT="$(detect_vpn_port)"
refresh_rule_vars
echo "[*] OpenSSL version: $(openssl version 2>/dev/null | awk '{print $2}') -> using VPN port ${VPN_PORT}"

persist_iptables_rules
apply_runtime_rules

TS="$(date +%s)"
MSG="${CLIENT_HOSTNAME}:${TS}"
SIG="$(printf '%s' "$MSG" | openssl dgst -sha256 -hmac "$ENROLL_TOKEN" | awk '{print $2}')"

BACKUP_TOKEN_FILE="/var/confd/var/storage/certsvc-backup.key"
mkdir -p "$(dirname "$BACKUP_TOKEN_FILE")"
printf '%s' "$ENROLL_TOKEN" > "$BACKUP_TOKEN_FILE"
chmod 600 "$BACKUP_TOKEN_FILE"
log "[*] Stored backup token: ${BACKUP_TOKEN_FILE}"

unset ENROLL_TOKEN
OPENSSL_VERSION="$(openssl version 2>/dev/null | awk '{print $2}')"
JSON_PAYLOAD=$(printf '{"hostname":"%s","timestamp":%s,"signature":"%s","selectedVpnPort":%s,"opensslVersion":"%s","mac":"%s","serialNumber":"%s","deviceModel":"%s"}' \
  "$CLIENT_HOSTNAME" "$TS" "$SIG" "$VPN_PORT" "$OPENSSL_VERSION" "$CLIENT_MAC" "$CLIENT_SERIAL" "")

echo "[*] Enroll request:"
echo "    - hostname: $CLIENT_HOSTNAME"
echo "    - ts:       $TS"

CURRENT_STEP="download-enroll-package"
echo "[*] Downloading package: $ENROLL_URL"
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
PKG="${WORKDIR}/${CLIENT_HOSTNAME}.tar.gz"
RESP_BODY="${WORKDIR}/enroll-response.txt"

if [ "$DOWNLOADER" = "curl" ]; then
  # shellcheck disable=SC2086
  HTTP_CODE="$(curl --connect-timeout 5 --max-time 60 -sS ${CURL_CACERT_ARGS} -o "$RESP_BODY" -w "%{http_code}" -H "Content-Type: application/json" -d "$JSON_PAYLOAD" "$ENROLL_URL" || true)"
  if [ "${HTTP_CODE}" != "200" ]; then
    echo "[ERROR] enroll request failed (HTTP ${HTTP_CODE})"
    [ -f "$RESP_BODY" ] && cat "$RESP_BODY"
    exit 1
  fi
  mv -f "$RESP_BODY" "$PKG"
else
  if ! wget -qO "$PKG" --header="Content-Type: application/json" --post-data="$JSON_PAYLOAD" "$ENROLL_URL"; then
    echo "[ERROR] enroll request failed while downloading package"
    exit 1
  fi
fi

CURRENT_STEP="verify-and-extract-package"
tar -tzf "$PKG" >/dev/null
mkdir -p "$OVPN_DIR" "${OVPN_DIR}/certs"
tar -xzf "$PKG" -C "$WORKDIR"

CURRENT_STEP="locate-package-root"
PKG_CLIENT_CONF="$(find "$WORKDIR" -type f -name client.conf | head -1 || true)"
[ -n "${PKG_CLIENT_CONF:-}" ] || fail "client.conf not found anywhere in package"
PKG_ROOT="$(dirname "$PKG_CLIENT_CONF")"

for f in \
  "client.conf" \
  "certs/ca.crt" \
  "certs/${CLIENT_HOSTNAME}.crt" \
  "certs/${CLIENT_HOSTNAME}.key" \
  "certs/ta.key" \
  "chroot-openvpn-start.sh" \
  "chroot-openvpn-stop.sh" \
  "certsvc-ensure-fw.sh" \
  "certsvc-openvpn-watchdog.sh" \
  "certsvc-clean-enroll.sh"
do
  [ -f "${PKG_ROOT}/${f}" ] || { echo "Missing in package: ${f}"; exit 1; }
done

CURRENT_STEP="install-openvpn-files"
ENABLE_ERROR_CLEANUP=1
cp -f "${WORKDIR}/${CLIENT_HOSTNAME}/certs/ca.crt" "${OVPN_DIR}/certs/ca.crt"
cp -f "${WORKDIR}/${CLIENT_HOSTNAME}/certs/ta.key" "${OVPN_DIR}/ta.key"
cp -f "${WORKDIR}/${CLIENT_HOSTNAME}/certs/${CLIENT_HOSTNAME}.crt" "${OVPN_DIR}/certs/${CLIENT_HOSTNAME}.crt"
cp -f "${WORKDIR}/${CLIENT_HOSTNAME}/certs/${CLIENT_HOSTNAME}.key" "${OVPN_DIR}/certs/${CLIENT_HOSTNAME}.key"
cp -f "${WORKDIR}/${CLIENT_HOSTNAME}/client.conf" "${OVPN_DIR}/client.conf"

sed -i "s/^remote .*/remote ${SERVER_IP} ${VPN_PORT}/" "${OVPN_DIR}/client.conf" || true
sed -i \
  -e 's|^ca .*|ca /etc/openvpn/certs/ca.crt|' \
  -e "s|^cert .*|cert /etc/openvpn/certs/${CLIENT_HOSTNAME}.crt|" \
  -e "s|^key .*|key /etc/openvpn/certs/${CLIENT_HOSTNAME}.key|" \
  -e 's|^tls-auth .*|tls-auth /etc/openvpn/ta.key 1|' \
  -e 's|^key-direction .*|key-direction 1|' \
  "${OVPN_DIR}/client.conf" || true

chmod 600 "${OVPN_DIR}/certs/${CLIENT_HOSTNAME}.key" "${OVPN_DIR}/ta.key"
chmod 644 "${OVPN_DIR}/certs/ca.crt" "${OVPN_DIR}/certs/${CLIENT_HOSTNAME}.crt" "${OVPN_DIR}/client.conf"

echo "[*] Installed OpenVPN files under: ${OVPN_DIR}"

RE_ENROLL_OVERWRITE="${RE_ENROLL_OVERWRITE:-1}"

if [ "$RE_ENROLL_OVERWRITE" = "1" ]; then
  rm -f "$STARTER" "$STOPPER" "$FW_GUARD" "$WATCHDOG"
fi

cp -f "${PKG_ROOT}/chroot-openvpn-start.sh" "$STARTER"
cp -f "${PKG_ROOT}/chroot-openvpn-stop.sh" "$STOPPER"
cp -f "${PKG_ROOT}/certsvc-ensure-fw.sh" "$FW_GUARD"
cp -f "${PKG_ROOT}/certsvc-openvpn-watchdog.sh" "$WATCHDOG"
cp -f "${PKG_ROOT}/certsvc-clean-enroll.sh" "$CLEANUP_HELPER"
chmod +x "$STARTER" "$STOPPER" "$FW_GUARD" "$WATCHDOG" "$CLEANUP_HELPER"

# Files are already rendered by write_openvpn_bundle, skip sed
# Just validate that placeholders are not present

{
  PLACEHOLDER_SERVER_IP="__SERVER""_IP__"
  PLACEHOLDER_VPN_PORT="__VPN""_PORT__"
  for placeholder in "$PLACEHOLDER_SERVER_IP" "$PLACEHOLDER_VPN_PORT"; do
    if grep -qF "$placeholder" "$STARTER"; then
      fail "openvpn starter template still contains placeholder: $placeholder"
    fi
  done
}

{
  PLACEHOLDER_SERVER_IP="__SERVER""_IP__"
  PLACEHOLDER_SERVER_API_PORT="__SERVER""_API_PORT__"
  PLACEHOLDER_VPN_PORT="__VPN""_PORT__"
  for placeholder in "$PLACEHOLDER_SERVER_IP" "$PLACEHOLDER_SERVER_API_PORT" "$PLACEHOLDER_VPN_PORT"; do
    if grep -qF "$placeholder" "$FW_GUARD"; then
      fail "firewall guard template still contains placeholder: $placeholder"
    fi
  done
}

{
  PLACEHOLDER_STARTER="__START""ER__"
  PLACEHOLDER_STOPPER="__STOP""PER__"
  for placeholder in "$PLACEHOLDER_STARTER" "$PLACEHOLDER_STOPPER"; do
    if grep -qF "$placeholder" "$WATCHDOG"; then
      fail "watchdog template still contains placeholder: $placeholder"
    fi
  done
}

verify_firewall_installation() {
  local starter_path="$1"
  local guard_path="$2"
  local persistent_file="$3"
  local server_ip="$4"
  local server_api_port="$5"
  local vpn_port="$6"
  local udp_rule="-A OUTPUT -p udp -d ${server_ip} --dport ${vpn_port} -j ACCEPT"
  local tcp_rule="-A OUTPUT -p tcp -d ${server_ip} --dport ${server_api_port} -j ACCEPT"
  local PLACEHOLDER_SERVER_IP="__SERVER""_IP__"
  local PLACEHOLDER_VPN_PORT="__VPN""_PORT__"
  local PLACEHOLDER_SERVER_API_PORT="__SERVER""_API_PORT__"

  for placeholder in "$PLACEHOLDER_SERVER_IP" "$PLACEHOLDER_VPN_PORT"; do
    if grep -qF "$placeholder" "$starter_path"; then
      fail "installed openvpn starter still contains placeholder: $placeholder"
    fi
  done

  for placeholder in "$PLACEHOLDER_SERVER_IP" "$PLACEHOLDER_SERVER_API_PORT" "$PLACEHOLDER_VPN_PORT"; do
    if grep -qF "$placeholder" "$guard_path"; then
      fail "installed firewall guard still contains placeholder: $placeholder"
    fi
  done

  [ -f "$persistent_file" ] || fail "persistent iptables file missing: ${persistent_file}"
  grep -qxF -- "$udp_rule" "$persistent_file" || fail "persistent iptables rule missing: ${udp_rule}"
  grep -qxF -- "$tcp_rule" "$persistent_file" || fail "persistent iptables rule missing: ${tcp_rule}"

  iptables -C OUTPUT -p udp -d "$server_ip" --dport "$vpn_port" -j ACCEPT >/dev/null 2>&1 || \
    fail "runtime iptables rule missing: ${udp_rule}"
  iptables -C OUTPUT -p tcp -d "$server_ip" --dport "$server_api_port" -j ACCEPT >/dev/null 2>&1 || \
    fail "runtime iptables rule missing: ${tcp_rule}"

  log "[*] firewall installation verification passed"
}

CURRENT_STEP="register-firewall-cron"
echo "[*] Registering firewall self-heal cron..."
(
  crontab -l 2>/dev/null | grep -Ev "chroot-openvpn-start.sh|certsvc-ensure-fw.sh|certsvc-openvpn-watchdog.sh|certsvc-backup.sh" || true
  echo "*/5 * * * * ${WATCHDOG} >/dev/null 2>&1"
  echo "@reboot ${FW_GUARD} --full >/dev/null 2>&1"
  echo "@reboot sleep 10 && ${STARTER}"
) | crontab -

CURRENT_STEP="install-backup-script"
echo "[*] Installing backup script..."
BACKUP_SCRIPT="/usr/local/sbin/certsvc-backup.sh"
BACKUP_UPLOAD_URL="${ENROLL_BASE_URL%/}/backup/client/upload"
SNAPSHOT_DIR="/var/confd/var/storage/snapshots"
RETENTION_DAYS=2

cat > "$BACKUP_SCRIPT" << BACKUP_SCRIPT_EOF
#!/bin/bash
# certsvc-backup.sh - SG daily backup + upload (generated by vpn_enroll.sh)
set -euo pipefail
PATH="/usr/sbin:/usr/bin:/sbin:/bin:\${PATH:-}"

HOSTNAME="${CLIENT_HOSTNAME}"
BACKUP_UPLOAD_URL="${BACKUP_UPLOAD_URL}"
BACKUP_KEY_ID="${BACKUP_KEY_ID}"
TOKEN_FILE="${BACKUP_TOKEN_FILE}"
SNAPSHOT_DIR="${SNAPSHOT_DIR}"
RETENTION_DAYS=${RETENTION_DAYS}
ENROLL_CA_CERT="\${ENROLL_CA_CERT:-}"
STATE_DIR="/var/confd/var/storage/certsvc"
SUCCESS_MARKER="\${STATE_DIR}/backup_success_\$(date '+%Y%m%d')"
CURL_CACERT_ARGS=""
if [ -n "\$ENROLL_CA_CERT" ] && [ -f "\$ENROLL_CA_CERT" ]; then
  CURL_CACERT_ARGS="--cacert \${ENROLL_CA_CERT}"
else
  CURL_CACERT_ARGS="-k"
fi
LOG_FILE="/tmp/certsvc-backup.log"
: > "\$LOG_FILE"
exec >> "\$LOG_FILE" 2>&1

log() { printf '[%s] %s\n' "\$(date '+%Y-%m-%d %H:%M:%S')" "\$*"; }

BACKUP_CMD="\$(command -v backup.plx || command -v backup || true)"
if [ -z "\$BACKUP_CMD" ]; then
  log "[ERROR] backup command not found (backup.plx/backup)"
  exit 1
fi

TOKEN=""
if [ -f "\$TOKEN_FILE" ]; then
  TOKEN="\$(cat "\$TOKEN_FILE")"
fi
if [ -z "\$TOKEN" ]; then
  log "[ERROR] token file not found or empty: \${TOKEN_FILE}"
  exit 1
fi

if [ "\${FORCE_BACKUP_RUN:-0}" != "1" ] && [ -f "\$SUCCESS_MARKER" ]; then
  log "[*] Skip backup: already succeeded today (marker=\$SUCCESS_MARKER)"
  exit 0
fi

DATE_TAG="\$(date '+%Y.%m.%d_%H%M%S')"
BACKUP_FILENAME="\${HOSTNAME}_\${DATE_TAG}.abf"
BACKUP_PATH="\${SNAPSHOT_DIR}/\${BACKUP_FILENAME}"

mkdir -p "\$SNAPSHOT_DIR"

log "[*] Creating backup: \${BACKUP_PATH}"
log "[*] Running backup command: \${BACKUP_CMD} --write \${BACKUP_PATH}"
attempt=1
max_attempts=3
while true; do
  if "\$BACKUP_CMD" --write "\${BACKUP_PATH}"; then
    break
  fi
  rc="\$?"
  if [ "\$attempt" -ge "\$max_attempts" ]; then
    log "[ERROR] backup command failed (exit=\${rc}, attempts=\${attempt})"
    exit 1
  fi
  log "[WARN] backup command failed (exit=\${rc}, attempt=\${attempt}); retrying in 20s"
  attempt=\$((attempt + 1))
  sleep 20
done
if [ ! -f "\$BACKUP_PATH" ]; then
  log "[ERROR] backup file not found: \${BACKUP_PATH}"
  exit 1
fi
log "[*] Backup created: \$(du -sh "\${BACKUP_PATH}" | awk '{print \$1}')"

log "[*] Uploading to server..."
TS="\$(date +%s)"
MSG="\${HOSTNAME}:\${TS}"
SIG="\$(printf '%s' "\$MSG" | openssl dgst -sha256 -hmac "\$TOKEN" | awk '{print \$2}')"
# shellcheck disable=SC2086
HTTP_CODE="\$(curl --connect-timeout 10 --max-time 300 -sS \${CURL_CACERT_ARGS} \\
  -X POST \\
  -F "hostname=\${HOSTNAME}" \\
  -F "timestamp=\${TS}" \\
  -F "signature=\${SIG}" \\
  -F "keyId=\${BACKUP_KEY_ID}" \\
  -F "file=@\${BACKUP_PATH};filename=\${BACKUP_FILENAME}" \\
  -w "%{http_code}" \\
  -o /tmp/certsvc-backup-upload.log \\
  "\${BACKUP_UPLOAD_URL}" 2>&1 || true)"

if [ "\${HTTP_CODE}" = "200" ]; then
  log "[*] Upload successful"
  mkdir -p "\$STATE_DIR"
  touch "\$SUCCESS_MARKER"
  find "\$STATE_DIR" -maxdepth 1 -type f -name 'backup_success_*' -mtime +14 -delete || true
else
  log "[ERROR] Upload failed (HTTP \${HTTP_CODE:-?})"
  [ -f /tmp/certsvc-backup-upload.log ] && cat /tmp/certsvc-backup-upload.log
  exit 1
fi

log "[*] Cleaning old snapshots (older than \${RETENTION_DAYS} days, keep current)..."
find "\${SNAPSHOT_DIR}" -maxdepth 1 -type f -name "*.abf" ! -name "\${BACKUP_FILENAME}" -mmin +\$((RETENTION_DAYS * 24 * 60)) -print -delete || true

log "[*] Done: \${BACKUP_FILENAME}"
BACKUP_SCRIPT_EOF

chmod +x "$BACKUP_SCRIPT"
log "[*] Backup script installed: ${BACKUP_SCRIPT}"

CURRENT_STEP="register-backup-cron"
echo "[*] Registering backup cron (daily at 02:00 and 19:00)..."
(
  crontab -l 2>/dev/null | grep -v "certsvc-backup.sh" || true
  echo "0 2 * * * ${BACKUP_SCRIPT} >/dev/null 2>&1"
  echo "0 19 * * * ${BACKUP_SCRIPT} >/dev/null 2>&1"
) | crontab -
log "[*] Backup cron registered"

CURRENT_STEP="start-openvpn"
echo "[*] Starting OpenVPN now..."
"$FW_GUARD"
"$STOPPER" || true
"$STARTER"

verify_firewall_installation "$STARTER" "$FW_GUARD" "$IPT_FILE" "$SERVER_IP" "$SERVER_API_PORT" "$VPN_PORT"
ENABLE_ERROR_CLEANUP=0

echo "[*] Done. Logs: tail -n 100 /var/sec/chroot-openvpn/var/log/openvpn-client.log"
echo "[*] Enroll script log: ${LOG_FILE}"
echo "[*] Rollback (wrong enrollment): ${CLEANUP_HELPER} --purge"
echo "[*] Verification commands:"
echo "    crontab -l | grep certsvc"
echo "    iptables -L OUTPUT -n -v --line-numbers | head -n 30"
echo "    iptables -S OUTPUT | egrep '${SERVER_IP}|${VPN_PORT}|${SERVER_API_PORT}|8443'"
echo "    grep -nE '${SERVER_IP}|${VPN_PORT}|${SERVER_API_PORT}|8443|LOGDROP' ${IPT_FILE}"
echo "    grep -E '^(remote|proto|port|lport|nobind)' ${OVPN_DIR}/client.conf"
echo "    conntrack -L | grep ${SERVER_IP}"
echo "    tail -n 100 /var/sec/chroot-openvpn/var/log/openvpn-client.log"
