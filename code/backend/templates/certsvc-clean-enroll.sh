#!/bin/bash
set -euo pipefail

STARTER="/usr/local/sbin/chroot-openvpn-start.sh"
STOPPER="/usr/local/sbin/chroot-openvpn-stop.sh"
FW_GUARD="/usr/local/sbin/certsvc-ensure-fw.sh"
WATCHDOG="/usr/local/sbin/certsvc-openvpn-watchdog.sh"
SELF_PATH="/usr/local/sbin/certsvc-clean-enroll.sh"
BACKUP_SCRIPT="/usr/local/sbin/certsvc-backup.sh"
BACKUP_TOKEN_FILE="/var/confd/var/storage/certsvc-backup.key"

CHROOT="/var/sec/chroot-openvpn"
OVPN_DIR="/var/sec/chroot-openvpn/etc/openvpn"
OPENVPN_PID_FILE="${CHROOT}/run/openvpn-client.pid"
IPT_FILE_DEFAULT="/var/mdw/etc/iptables/iptable.filter"

ENROLL_LOG_FILE="/tmp/certsvc-enroll.log"
WATCHDOG_LOG_FILE="/tmp/certsvc-openvpn-watchdog.log"
BACKUP_LOG_FILE="/tmp/certsvc-backup.log"
BACKUP_UPLOAD_LOG_FILE="/tmp/certsvc-backup-upload.log"
WATCHDOG_STATE_FILE="/tmp/.certsvc-watchdog-state"
WORKDIR="/tmp/certsvc-enroll"

DRY_RUN=0
WITH_OPENVPN_FILES=0
WITH_BACKUP_ARTIFACTS=0
WITH_FIREWALL_RULES=0
WITH_TEMP_FILES=0
PURGE_MODE=0

usage() {
  cat <<'EOF'
Usage: certsvc-clean-enroll.sh [--dry-run] [--with-openvpn-files] [--with-backup-artifacts] [--with-firewall-rules] [--with-temp-files] [--purge]

Options:
  --dry-run             Show what would be removed.
  --with-openvpn-files  Also remove installed OpenVPN client files.
  --with-backup-artifacts
                        Also remove backup script/token created by vpn_enroll.sh.
  --with-firewall-rules Also remove runtime/persistent firewall rules created by vpn_enroll.sh.
  --with-temp-files     Also remove temporary logs/state/workdir files.
  --purge               Full rollback for wrong enrollment (enables all --with-* options).
  -h, --help            Show this help.
EOF
}

log() {
  printf '[%s] [certsvc-clean-enroll] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2
}

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --dry-run)
        DRY_RUN=1
        ;;
      --with-openvpn-files)
        WITH_OPENVPN_FILES=1
        ;;
      --with-backup-artifacts)
        WITH_BACKUP_ARTIFACTS=1
        ;;
      --with-firewall-rules)
        WITH_FIREWALL_RULES=1
        ;;
      --with-temp-files)
        WITH_TEMP_FILES=1
        ;;
      --purge)
        PURGE_MODE=1
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        log "invalid option: $1"
        usage
        exit 2
        ;;
    esac
    shift
  done

  if [ "$PURGE_MODE" -eq 1 ]; then
    WITH_OPENVPN_FILES=1
    WITH_BACKUP_ARTIFACTS=1
    WITH_FIREWALL_RULES=1
    WITH_TEMP_FILES=1
  fi
}

remove_file() {
  local file_path="$1"
  if [ ! -e "$file_path" ]; then
    return 0
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    log "[DRY-RUN] remove file: $file_path"
    return 0
  fi
  rm -f "$file_path"
  log "removed file: $file_path"
}

remove_dir() {
  local dir_path="$1"
  if [ ! -d "$dir_path" ]; then
    return 0
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    log "[DRY-RUN] remove directory: $dir_path"
    return 0
  fi
  rm -rf "$dir_path"
  log "removed directory: $dir_path"
}

remove_glob() {
  local pattern="$1"
  local matched=0
  local path

  shopt -s nullglob
  for path in $pattern; do
    matched=1
    remove_file "$path"
  done
  shopt -u nullglob

  if [ "$matched" -eq 0 ] && [ "$DRY_RUN" -eq 1 ]; then
    log "[DRY-RUN] no match for pattern: $pattern"
  fi
}

cleanup_crontab() {
  local tmp_cron
  local tmp_new
  local patterns='chroot-openvpn-start.sh|certsvc-ensure-fw.sh|certsvc-openvpn-watchdog.sh|certsvc-backup.sh'
  tmp_cron="$(mktemp /tmp/certsvc-clean-enroll.cron.XXXXXX)"
  tmp_new="${tmp_cron}.new"

  if ! crontab -l > "$tmp_cron" 2>/dev/null; then
    rm -f "$tmp_cron"
    return 0
  fi

  if [ "$DRY_RUN" -eq 1 ]; then
    grep -E "$patterns" "$tmp_cron" >/dev/null 2>&1 && \
      log "[DRY-RUN] would remove enroll-related crontab entries"
    rm -f "$tmp_cron"
    return 0
  fi

  grep -Ev "$patterns" "$tmp_cron" > "$tmp_new" || true
  crontab "$tmp_new"
  rm -f "$tmp_cron" "$tmp_new"
  log "cleaned enroll-related crontab entries"
}

extract_fw_value() {
  local key="$1"
  local script="$2"
  sed -n "s/^${key}=\"\([^\"]*\)\"/\1/p" "$script" | head -1
}

delete_runtime_rule_all() {
  local chain="$1"
  local proto="$2"
  local dst="$3"
  local dport="$4"

  while iptables -C "$chain" -p "$proto" -d "$dst" --dport "$dport" -j ACCEPT >/dev/null 2>&1; do
    if [ "$DRY_RUN" -eq 1 ]; then
      log "[DRY-RUN] remove runtime iptables rule: -A ${chain} -p ${proto} -d ${dst} --dport ${dport} -j ACCEPT"
      break
    fi
    iptables -D "$chain" -p "$proto" -d "$dst" --dport "$dport" -j ACCEPT >/dev/null 2>&1 || break
    log "removed runtime iptables rule: -A ${chain} -p ${proto} -d ${dst} --dport ${dport} -j ACCEPT"
  done
}

cleanup_firewall_rules() {
  local server_ip=""
  local server_api_port=""
  local vpn_port=""
  local ipt_file="$IPT_FILE_DEFAULT"
  local udp_rule
  local tcp_rule
  local tmp_file
  local backup_file

  if [ ! -f "$FW_GUARD" ]; then
    log "firewall guard not found, skipping firewall cleanup"
    return 0
  fi

  server_ip="$(extract_fw_value "SERVER_IP" "$FW_GUARD" || true)"
  server_api_port="$(extract_fw_value "SERVER_API_PORT" "$FW_GUARD" || true)"
  vpn_port="$(extract_fw_value "VPN_PORT" "$FW_GUARD" || true)"
  ipt_file="$(extract_fw_value "IPT_FILE" "$FW_GUARD" || true)"
  [ -n "$ipt_file" ] || ipt_file="$IPT_FILE_DEFAULT"

  if [ -z "$server_ip" ] || [ -z "$server_api_port" ] || [ -z "$vpn_port" ]; then
    log "failed to parse firewall rule variables from $FW_GUARD, skipping firewall cleanup"
    return 0
  fi

  udp_rule="-A OUTPUT -p udp -d ${server_ip} --dport ${vpn_port} -j ACCEPT"
  tcp_rule="-A OUTPUT -p tcp -d ${server_ip} --dport ${server_api_port} -j ACCEPT"

  delete_runtime_rule_all OUTPUT udp "$server_ip" "$vpn_port"
  delete_runtime_rule_all OUTPUT tcp "$server_ip" "$server_api_port"

  if [ ! -f "$ipt_file" ]; then
    log "persistent iptables file not found, skipping: $ipt_file"
    return 0
  fi

  if [ "$DRY_RUN" -eq 1 ]; then
    grep -qxF -- "$udp_rule" "$ipt_file" && log "[DRY-RUN] remove persistent rule: $udp_rule"
    grep -qxF -- "$tcp_rule" "$ipt_file" && log "[DRY-RUN] remove persistent rule: $tcp_rule"
    return 0
  fi

  tmp_file="$(mktemp "$(dirname "$ipt_file")/.certsvc-clean-iptable.XXXXXX")"
  awk -v r1="$udp_rule" -v r2="$tcp_rule" '
  {
    if ($0 == r1 || $0 == r2) next
    print
  }
  ' "$ipt_file" > "$tmp_file"

  backup_file="${ipt_file}.bak.$(date '+%Y%m%d%H%M%S')"
  cp -p "$ipt_file" "$backup_file"
  mv -f "$tmp_file" "$ipt_file"
  log "cleaned persistent iptables rules (backup: $backup_file)"
}

stop_openvpn() {
  if [ -x "$STOPPER" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
      log "[DRY-RUN] stop OpenVPN via $STOPPER"
    else
      "$STOPPER" || true
      log "stopped OpenVPN via $STOPPER"
    fi
    return 0
  fi

  if [ -f "$OPENVPN_PID_FILE" ]; then
    local pid
    pid="$(cat "$OPENVPN_PID_FILE" 2>/dev/null || true)"
    if [[ "$pid" =~ ^[0-9]+$ ]]; then
      if [ "$DRY_RUN" -eq 1 ]; then
        log "[DRY-RUN] kill OpenVPN pid: $pid"
      else
        kill "$pid" 2>/dev/null || true
        rm -f "$OPENVPN_PID_FILE"
        log "stopped OpenVPN pid: $pid"
      fi
    fi
  fi
}

cleanup_openvpn_files() {
  stop_openvpn

  local f
  for f in \
    "${OVPN_DIR}/client.conf" \
    "${OVPN_DIR}/ta.key" \
    "${OVPN_DIR}/certs/ca.crt"
  do
    remove_file "$f"
  done

  remove_glob "${OVPN_DIR}/certs/*.crt"
  remove_glob "${OVPN_DIR}/certs/*.key"
}

cleanup_backup_artifacts() {
  remove_file "$BACKUP_SCRIPT"
  remove_file "$BACKUP_TOKEN_FILE"
}

cleanup_temp_files() {
  remove_file "$ENROLL_LOG_FILE"
  remove_file "$WATCHDOG_LOG_FILE"
  remove_file "$BACKUP_LOG_FILE"
  remove_file "$BACKUP_UPLOAD_LOG_FILE"
  remove_file "$WATCHDOG_STATE_FILE"
  remove_dir "$WORKDIR"
}

main() {
  parse_args "$@"

  remove_file "$STARTER"
  remove_file "$STOPPER"
  remove_file "$FW_GUARD"
  remove_file "$WATCHDOG"
  cleanup_crontab

  if [ "$WITH_FIREWALL_RULES" -eq 1 ]; then
    cleanup_firewall_rules
  fi

  if [ "$WITH_OPENVPN_FILES" -eq 1 ]; then
    cleanup_openvpn_files
  fi

  if [ "$WITH_BACKUP_ARTIFACTS" -eq 1 ]; then
    cleanup_backup_artifacts
  fi

  if [ "$WITH_TEMP_FILES" -eq 1 ]; then
    cleanup_temp_files
  fi

  if [ "$DRY_RUN" -eq 0 ] && [ -e "$SELF_PATH" ]; then
    log "cleanup helper kept: $SELF_PATH"
  fi

  log "done"
}

main "$@"
