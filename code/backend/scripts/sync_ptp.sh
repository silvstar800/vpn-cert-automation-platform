#!/usr/bin/env bash
set -euo pipefail

ENV_FILE=/opt/certsvc/.env

get_env() {
  local key="$1"
  local def="$2"
  local val
  if [ -n "${!key-}" ]; then
    echo "${!key}"
    return
  fi
  val=$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -n1 | cut -d'=' -f2- || true)
  if [ -z "${val}" ]; then
    echo "$def"
  else
    echo "$val"
  fi
}

print_help() {
  cat <<'EOF'
Usage:
  sync_ptp
  sync_ptp --dry-run
  sync_ptp dry-run
  sync_ptp --cleanup-stale-only
  sync_ptp --cleanup-stale-safe
  sync_ptp --apply-ccd atech-HQ --apply-ccd barun-orthopedics
  sync_ptp --apply-ccd 'atech-*'
  sync_ptp --dry-run --mismatch-limit 50
  sync_ptp --mismatch-limit=50 --dry-run
  sync_ptp --dry-run --compare-route
  sync_ptp --help
  sync_ptp help

Description:
  DB(ip_leases + clients) 기준으로 OpenVPN CCD와 server conf를 재생성/검증/적용합니다.
  본 스크립트의 SG(openvpn) 기준은 non-legacy(is_legacy=false)이며,
  SG-legacy(is_legacy=true)는 CCD_LEGACY_DIR에서 별도 비교/정리합니다.
  기본 실행은 임시 경로에서 생성/검증 후에만 운영 파일을 교체합니다.

Modes:
  default:
    실제 반영 모드입니다.
    1) 임시 CCD/conf 생성
    2) 정적 검증(+ 가능 시 openvpn --test-parse)
    3) 운영 CCD/conf 원자적 교체
    4) 서비스 reload (실패 시 옵션에 따라 restart fallback)
    5) 실패 시 rollback

  --dry-run | dry-run:
    운영 반영 없이 비교/검증만 수행합니다.
    - DB 기대값 vs 현재 CCD 불일치 출력
      - missing_in_current: DB에는 있으나 현재 운영 CCD에 없음
      - stale_in_current: DB에는 없으나 운영 CCD에 남아있음
      - content_diff: 파일명은 같지만 내용이 다름
    - DB 기대 identity vs PKI issued cert 존재 여부 비교
      - missing_in_pki: DB/CCD에는 있으나 issued cert가 없음
    - route(conf) 비교는 기본 비활성, --compare-route로만 수행
    - 마지막에 dry-run mismatch total 요약 출력
    - mismatch-limit 설정 시 항목별 상위 N개만 출력하고 생략 개수 표시

  --cleanup-stale-only:
    stale_in_current 항목만 실제 정리합니다.
    - DB 기대 hostname 목록에 없는 CCD 파일만 삭제
    - missing_in_current/content_diff/conf는 변경하지 않음
    - stale 파일이 실제로 삭제된 경우 OpenVPN 서비스 reload 수행

  --cleanup-stale-safe:
    stale_in_current 항목만 보수적으로 정리합니다.
    - stale 파일을 삭제하지 않고 백업 디렉터리로 이동
    - 이동 경로: SYNC_PTP_STALE_BACKUP_ROOT/<timestamp>/<vpn_type>/
    - 이동이 발생한 경우 OpenVPN 서비스 reload 수행

  --apply-ccd <identity|glob>:
    지정한 identity CCD만 선택 반영합니다(여러 번 지정 가능).
    - DB 기반 기대 CCD 내용으로 해당 파일만 갱신
    - glob 패턴(* ?) 사용 가능: 예) 'atech-*', '*-hospital'
    - conf/route/stale 정리는 수행하지 않음
    - 변경이 발생한 vpn_type 서비스만 reload 수행

Environment Variables:
  CCD_DIR                         (default: /etc/openvpn/ccd)
  CCD_LEGACY_DIR                  (default: /etc/openvpn/ccd-legacy)
  CCD_SFOS_DIR                    (default: /etc/openvpn/ccd-sfos)
  OPENVPN_SERVER_CONF             (default: /etc/openvpn/server/server.conf)
  SFOS_SERVER_CONF                (default: /etc/openvpn/server/server-sfos.conf)
  OPENVPN_TUN_SERIAL_IP           (default: 10.242.254.1)
  OPENVPN_LEGACY_TUN_SERIAL_IP    (default: 10.242.253.1)
  SFOS_TUN_SERIAL_IP              (default: 10.242.255.1)
  OPENVPN_PUSH_REMOTE_NETWORK_1   (default: 10.0.200.4)
  PKI_DIR                         (default: /etc/openvpn/pki)
  OPENVPN_SERVICE                 (default: openvpn-server@server.service)
  OPENVPN_LEGACY_SERVICE          (default: openvpn-server@server-legacy.service)
  SFOS_OPENVPN_SERVICE            (default: openvpn-server@server-sfos.service)
  OPENVPN_ALLOW_RESTART_FALLBACK  (default: 0)
  SYNC_PTP_DRY_RUN                (default: 0)
  SYNC_PTP_MISMATCH_MAX           (default: 0, 0=unlimited)
  SYNC_PTP_COMPARE_ROUTE          (default: 0)
  SYNC_PTP_CLEANUP_STALE_ONLY     (default: 0)
  SYNC_PTP_STALE_SAFE_MODE        (default: 0)
  SYNC_PTP_STALE_BACKUP_ROOT      (default: /opt/certsvc/backups/client_backups/stale_ccd)
  SYNC_PTP_APPLY_CCD_LIST         (default: empty, comma-separated)

Notes:
  - 기본값은 reload 우선이며, restart fallback은 OPENVPN_ALLOW_RESTART_FALLBACK=1일 때만 동작합니다.
  - CLI 인자 --dry-run 은 .env 값보다 우선 적용됩니다.
  - --mismatch-limit N 은 dry-run 상세 출력만 제한하며, total 집계는 전체 기준입니다.
  - route 비교는 conf가 전체 대역을 포함하는 운영 환경을 고려해 기본 비활성입니다.
  - --cleanup-stale-only 는 stale 파일만 정리하며 conf/route는 건드리지 않습니다.
  - --cleanup-stale-safe 는 stale 파일을 백업 경로로 이동하는 보수 모드입니다.
  - --apply-ccd 는 지정한 파일만 선택 반영합니다. glob 패턴('atech-*') 사용 시 쉘 확장 방지를 위해 따옴표 권장.
EOF
}

APPLY_CCD_CLI_LIST=()

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --dry-run|dry-run)
        export SYNC_PTP_DRY_RUN=1
        ;;
      --mismatch-limit)
        shift
        [ "$#" -gt 0 ] || { echo "ERROR: --mismatch-limit requires a value" >&2; exit 2; }
        export SYNC_PTP_MISMATCH_MAX="$1"
        ;;
      --mismatch-limit=*)
        export SYNC_PTP_MISMATCH_MAX="${1#*=}"
        ;;
      --compare-route)
        export SYNC_PTP_COMPARE_ROUTE=1
        ;;
      --cleanup-stale-only|cleanup-stale-only)
        export SYNC_PTP_CLEANUP_STALE_ONLY=1
        ;;
      --cleanup-stale-safe|cleanup-stale-safe)
        export SYNC_PTP_CLEANUP_STALE_ONLY=1
        export SYNC_PTP_STALE_SAFE_MODE=1
        ;;
      --apply-ccd)
        shift
        [ "$#" -gt 0 ] || { echo "ERROR: --apply-ccd requires an identity" >&2; exit 2; }
        APPLY_CCD_CLI_LIST+=("$1")
        ;;
      --apply-ccd=*)
        APPLY_CCD_CLI_LIST+=("${1#*=}")
        ;;
      -h|--help|help)
        print_help
        exit 0
        ;;
      *)
        echo "ERROR: unknown argument: $1" >&2
        echo "Try: sync_ptp --help" >&2
        exit 2
        ;;
    esac
    shift
  done
}

parse_args "$@"

CCD_DIR=$(get_env CCD_DIR /etc/openvpn/ccd)
CCD_LEGACY_DIR=$(get_env CCD_LEGACY_DIR /etc/openvpn/ccd-legacy)
CCD_SFOS_DIR=$(get_env CCD_SFOS_DIR /etc/openvpn/ccd-sfos)
OPENVPN_SERVER_CONF=$(get_env OPENVPN_SERVER_CONF /etc/openvpn/server/server.conf)
SFOS_SERVER_CONF=$(get_env SFOS_SERVER_CONF /etc/openvpn/server/server-sfos.conf)
OPENVPN_TUN_SERIAL_IP=$(get_env OPENVPN_TUN_SERIAL_IP 10.242.254.1)
OPENVPN_LEGACY_TUN_SERIAL_IP=$(get_env OPENVPN_LEGACY_TUN_SERIAL_IP 10.242.253.1)
SFOS_TUN_SERIAL_IP=$(get_env SFOS_TUN_SERIAL_IP 10.242.255.1)
REMOTE_NET=$(get_env OPENVPN_PUSH_REMOTE_NETWORK_1 10.0.200.4)
PKI_DIR=$(get_env PKI_DIR /etc/openvpn/pki)
OPENVPN_SERVICE=$(get_env OPENVPN_SERVICE openvpn-server@server.service)
OPENVPN_LEGACY_SERVICE=$(get_env OPENVPN_LEGACY_SERVICE openvpn-server@server-legacy.service)
SFOS_OPENVPN_SERVICE=$(get_env SFOS_OPENVPN_SERVICE openvpn-server@server-sfos.service)
OPENVPN_ALLOW_RESTART_FALLBACK=$(get_env OPENVPN_ALLOW_RESTART_FALLBACK 0)
SYNC_PTP_DRY_RUN=$(get_env SYNC_PTP_DRY_RUN 0)
SYNC_PTP_MISMATCH_MAX=$(get_env SYNC_PTP_MISMATCH_MAX 0)
SYNC_PTP_COMPARE_ROUTE=$(get_env SYNC_PTP_COMPARE_ROUTE 0)
SYNC_PTP_CLEANUP_STALE_ONLY=$(get_env SYNC_PTP_CLEANUP_STALE_ONLY 0)
SYNC_PTP_STALE_SAFE_MODE=$(get_env SYNC_PTP_STALE_SAFE_MODE 0)
SYNC_PTP_STALE_BACKUP_ROOT=$(get_env SYNC_PTP_STALE_BACKUP_ROOT /opt/certsvc/backups/client_backups/stale_ccd)
SYNC_PTP_APPLY_CCD_LIST=$(get_env SYNC_PTP_APPLY_CCD_LIST "")

TS=$(date +%Y%m%d_%H%M%S)
TMP_ROOT=$(mktemp -d /tmp/sync_ptp.XXXXXX)
TMP_CCD_DIR="$TMP_ROOT/ccd"
TMP_CCD_LEGACY_DIR="$TMP_ROOT/ccd-legacy"
TMP_CCD_SFOS_DIR="$TMP_ROOT/ccd-sfos"
TMP_OPENVPN_SERVER_CONF="$TMP_ROOT/server.conf"
TMP_SFOS_SERVER_CONF="$TMP_ROOT/server-sfos.conf"

OPENVPN_CONF_BAK=""
SFOS_CONF_BAK=""
CCD_OLD_DIR=""
CCD_SFOS_OLD_DIR=""
CURRENT_STAGE="init"
DRY_RUN_MISMATCH_COUNT=0

log() {
  echo "[sync_ptp] $*"
}

cleanup() {
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

fail() {
  echo "ERROR: [$CURRENT_STAGE] $*" >&2
  exit 1
}

validate_non_negative_integer() {
  local name="$1"
  local value="$2"
  if ! [[ "$value" =~ ^[0-9]+$ ]]; then
    fail "$name must be a non-negative integer (got=$value)"
  fi
}

validate_binary_flag() {
  local name="$1"
  local value="$2"
  if [ "$value" != "0" ] && [ "$value" != "1" ]; then
    fail "$name must be 0 or 1 (got=$value)"
  fi
}

collect_stale_files() {
  local expected_dir="$1"
  local current_dir="$2"
  local out_file="$3"
  local expected_list="$TMP_ROOT/$(basename "$out_file").expected"
  local current_list="$TMP_ROOT/$(basename "$out_file").current"

  find "$expected_dir" -maxdepth 1 -type f -printf '%f\n' | LC_ALL=C sort -u > "$expected_list"
  if [ -d "$current_dir" ]; then
    find "$current_dir" -maxdepth 1 -type f -printf '%f\n' | LC_ALL=C sort -u > "$current_list"
  else
    : > "$current_list"
  fi
  comm -13 "$expected_list" "$current_list" > "$out_file"
}

cleanup_stale_files() {
  local label="$1"
  local current_dir="$2"
  local stale_file="$3"
  local out_var="$4"
  local backup_dir=""
  local removed=0
  local f

  if [ "$SYNC_PTP_STALE_SAFE_MODE" = "1" ]; then
    backup_dir="${SYNC_PTP_STALE_BACKUP_ROOT}/${TS}/${label}"
    mkdir -p "$backup_dir"
  fi

  while IFS= read -r f; do
    [ -z "$f" ] && continue
    if [ -f "$current_dir/$f" ]; then
      if [ "$SYNC_PTP_STALE_SAFE_MODE" = "1" ]; then
        mv "$current_dir/$f" "$backup_dir/$f"
        log "cleanup-stale:$label moved file=$f to=$backup_dir/$f"
      else
        rm -f "$current_dir/$f"
        log "cleanup-stale:$label removed file=$f"
      fi
      removed=$((removed + 1))
    fi
  done < "$stale_file"

  if [ "$SYNC_PTP_STALE_SAFE_MODE" = "1" ]; then
    log "cleanup-stale:$label summary moved=$removed"
  else
    log "cleanup-stale:$label summary removed=$removed"
  fi
  printf -v "$out_var" '%s' "$removed"
}

apply_selected_ccd_files() {
  local combined_csv="$SYNC_PTP_APPLY_CCD_LIST"
  local ident
  local src
  local dst
  local changed_openvpn=0
  local changed_openvpn_legacy=0
  local changed_sfos=0
  local -a apply_list

  for ident in "${APPLY_CCD_CLI_LIST[@]}"; do
    if [ -n "$combined_csv" ]; then
      combined_csv+=","
    fi
    combined_csv+="$ident"
  done

  IFS=',' read -r -a apply_list <<< "$combined_csv"
  if [ "${#apply_list[@]}" -eq 0 ]; then
    fail "--apply-ccd requires at least one identity"
  fi

  # glob 패턴 확장: * 또는 ? 를 포함한 항목은 TMP_CCD_DIR 기준으로 확장
  local -a expanded_list=()
  for ident in "${apply_list[@]}"; do
    ident=$(echo "$ident" | xargs)
    [ -n "$ident" ] || continue
    if [[ "$ident" == *'*'* ]] || [[ "$ident" == *'?'* ]]; then
      local -a matched=()
      for dir in "$TMP_CCD_DIR" "$TMP_CCD_LEGACY_DIR" "$TMP_CCD_SFOS_DIR"; do
        local f
        for f in "$dir"/$ident; do
          [ -f "$f" ] || continue
          local base
          base=$(basename "$f")
          # 중복 제거
          local already=0
          local e
          for e in "${expanded_list[@]}" "${matched[@]}"; do
            [ "$e" = "$base" ] && already=1 && break
          done
          [ "$already" -eq 0 ] && matched+=("$base")
        done
      done
      if [ "${#matched[@]}" -eq 0 ]; then
        fail "glob pattern matched no identities: $ident"
      fi
      log "apply-ccd: glob '$ident' expanded to: ${matched[*]}"
      expanded_list+=("${matched[@]}")
    else
      if [ ! -f "$TMP_CCD_DIR/$ident" ] && [ ! -f "$TMP_CCD_LEGACY_DIR/$ident" ] && [ ! -f "$TMP_CCD_SFOS_DIR/$ident" ]; then
        fail "identity not found in expected SG/SG-legacy/SFOS set: $ident"
      fi
      expanded_list+=("$ident")
    fi
  done
  apply_list=("${expanded_list[@]}")

  for ident in "${apply_list[@]}"; do
    ident=$(echo "$ident" | xargs)
    [ -n "$ident" ] || continue

    if [ -f "$TMP_CCD_DIR/$ident" ]; then
      src="$TMP_CCD_DIR/$ident"
      dst="$CCD_DIR/$ident"
      if [ ! -f "$dst" ]; then
        log "apply-ccd:openvpn skip(missing_in_current, use full sync) file=$ident"
      elif cmp -s "$src" "$dst"; then
        log "apply-ccd:openvpn unchanged file=$ident"
      else
        cp -a "$dst" "${dst}.bak.${TS}"
        cp -a "$src" "$dst"
        log "apply-ccd:openvpn updated(content_diff) file=$ident"
        changed_openvpn=1
      fi
    fi

    if [ -f "$TMP_CCD_SFOS_DIR/$ident" ]; then
      src="$TMP_CCD_SFOS_DIR/$ident"
      dst="$CCD_SFOS_DIR/$ident"
      if [ ! -f "$dst" ]; then
        log "apply-ccd:sfos skip(missing_in_current, use full sync) file=$ident"
      elif cmp -s "$src" "$dst"; then
        log "apply-ccd:sfos unchanged file=$ident"
      else
        cp -a "$dst" "${dst}.bak.${TS}"
        cp -a "$src" "$dst"
        log "apply-ccd:sfos updated(content_diff) file=$ident"
        changed_sfos=1
      fi
    fi

    if [ -f "$TMP_CCD_LEGACY_DIR/$ident" ]; then
      src="$TMP_CCD_LEGACY_DIR/$ident"
      dst="$CCD_LEGACY_DIR/$ident"
      if [ ! -f "$dst" ]; then
        log "apply-ccd:openvpn-legacy skip(missing_in_current, use full sync) file=$ident"
      elif cmp -s "$src" "$dst"; then
        log "apply-ccd:openvpn-legacy unchanged file=$ident"
      else
        cp -a "$dst" "${dst}.bak.${TS}"
        cp -a "$src" "$dst"
        log "apply-ccd:openvpn-legacy updated(content_diff) file=$ident"
        changed_openvpn_legacy=1
      fi
    fi
  done

  if [ "$changed_openvpn" = "1" ]; then
    reload_or_restart_service "$OPENVPN_SERVICE" || fail "service apply failed after apply-ccd: $OPENVPN_SERVICE"
  fi
  if [ "$changed_sfos" = "1" ]; then
    reload_or_restart_service "$SFOS_OPENVPN_SERVICE" || fail "service apply failed after apply-ccd: $SFOS_OPENVPN_SERVICE"
  fi
  if [ "$changed_openvpn_legacy" = "1" ]; then
    reload_or_restart_service "$OPENVPN_LEGACY_SERVICE" || fail "service apply failed after apply-ccd: $OPENVPN_LEGACY_SERVICE"
  fi

  log "apply-ccd complete: reloaded_openvpn=$changed_openvpn reloaded_openvpn_legacy=$changed_openvpn_legacy reloaded_sfos=$changed_sfos"
}

backup_conf_if_exists() {
  local src="$1"
  local dst="$2"
  if [ -f "$src" ]; then
    cp -a "$src" "$dst"
  fi
}

validate_conf_static() {
  local conf="$1"
  local expected_ccd="$2"
  local ccd_value
  local v

  ccd_value=$(awk '$1=="client-config-dir"{print $2}' "$conf" | tail -n1)
  [ -n "$ccd_value" ] || fail "missing client-config-dir in $conf"
  [ "$ccd_value" = "$expected_ccd" ] || fail "client-config-dir mismatch in $conf (got=$ccd_value expected=$expected_ccd)"

  for key in ca cert key dh; do
    v=$(awk -v k="$key" '$1==k{print $2}' "$conf" | tail -n1)
    [ -n "$v" ] || fail "missing '$key' directive in $conf"
    [ -f "$v" ] || fail "referenced file not found for '$key': $v"
  done

  v=$(awk '$1=="tls-auth"{print $2}' "$conf" | tail -n1)
  if [ -n "$v" ] && [ ! -f "$v" ]; then
    fail "referenced file not found for 'tls-auth': $v"
  fi
}

validate_conf_with_openvpn() {
  local conf="$1"

  if ! command -v openvpn >/dev/null 2>&1; then
    echo "WARN: openvpn binary not found; static validation only" >&2
    return 0
  fi

  if openvpn --help 2>&1 | grep -q -- "--test-parse"; then
    if ! openvpn --test-parse --config "$conf" --verb 0 >/dev/null 2>&1; then
      fail "openvpn --test-parse validation failed for $conf"
    fi
    return 0
  fi

  echo "WARN: --test-parse unsupported; static validation only for $conf" >&2
}

reload_or_restart_service() {
  local svc="$1"
  local can_reload="unknown"

  can_reload=$(systemctl show "$svc" -p CanReload --value 2>/dev/null || true)

  if [ "$can_reload" = "no" ]; then
    if [ "$OPENVPN_ALLOW_RESTART_FALLBACK" = "1" ]; then
      echo "WARN: service does not support reload (CanReload=no), trying restart: $svc" >&2
      if systemctl restart "$svc" >/dev/null 2>&1; then
        echo "service restarted: $svc"
        return 0
      fi
      echo "ERROR: restart failed for $svc" >&2
      return 1
    fi
    echo "ERROR: service does not support reload (CanReload=no): $svc" >&2
    echo "ERROR: set OPENVPN_ALLOW_RESTART_FALLBACK=1 to allow restart fallback" >&2
    return 1
  fi

  if systemctl reload "$svc" >/dev/null 2>&1; then
    echo "service reloaded: $svc"
    return 0
  fi

  if [ "$OPENVPN_ALLOW_RESTART_FALLBACK" = "1" ]; then
    echo "WARN: reload failed for $svc, trying restart" >&2
    if systemctl restart "$svc" >/dev/null 2>&1; then
      echo "service restarted: $svc"
      return 0
    fi
    echo "ERROR: restart failed for $svc" >&2
    return 1
  fi

  echo "ERROR: reload failed for $svc (restart fallback disabled)" >&2
  return 1
}

restore_on_failure() {
  local rc=0

  log "rollback start (stage=$CURRENT_STAGE)"

  if [ -n "$OPENVPN_CONF_BAK" ] && [ -f "$OPENVPN_CONF_BAK" ]; then
    log "rollback: restore $OPENVPN_SERVER_CONF from backup"
    cp -a "$OPENVPN_CONF_BAK" "$OPENVPN_SERVER_CONF" || rc=1
  fi
  if [ -n "$SFOS_CONF_BAK" ] && [ -f "$SFOS_CONF_BAK" ]; then
    log "rollback: restore $SFOS_SERVER_CONF from backup"
    cp -a "$SFOS_CONF_BAK" "$SFOS_SERVER_CONF" || rc=1
  fi

  if [ -n "$CCD_OLD_DIR" ] && [ -d "$CCD_OLD_DIR" ]; then
    log "rollback: restore $CCD_DIR from $CCD_OLD_DIR"
    rm -rf "$CCD_DIR"
    mv "$CCD_OLD_DIR" "$CCD_DIR" || rc=1
  fi
  if [ -n "$CCD_SFOS_OLD_DIR" ] && [ -d "$CCD_SFOS_OLD_DIR" ]; then
    log "rollback: restore $CCD_SFOS_DIR from $CCD_SFOS_OLD_DIR"
    rm -rf "$CCD_SFOS_DIR"
    mv "$CCD_SFOS_OLD_DIR" "$CCD_SFOS_DIR" || rc=1
  fi

  log "rollback: re-apply services"
  reload_or_restart_service "$OPENVPN_SERVICE" || rc=1
  reload_or_restart_service "$SFOS_OPENVPN_SERVICE" || rc=1

  if [ "$rc" -eq 0 ]; then
    log "rollback complete"
  else
    log "rollback completed with errors"
  fi

  return $rc
}

get_status_file_from_conf() {
  local conf="$1"
  awk '$1=="status"{print $2}' "$conf" | tail -n1
}

count_connected_clients() {
  local status_file="$1"
  if [ -z "$status_file" ]; then
    echo "n/a"
    return 0
  fi
  if [ ! -f "$status_file" ]; then
    echo "n/a"
    return 0
  fi
  grep -Ec '^CLIENT_LIST[,	]' "$status_file" || true
}

log_client_count_snapshot() {
  local phase="$1"
  local ovpn_status sfos_status
  local ovpn_count sfos_count

  ovpn_status=$(get_status_file_from_conf "$OPENVPN_SERVER_CONF")
  sfos_status=$(get_status_file_from_conf "$SFOS_SERVER_CONF")
  ovpn_count=$(count_connected_clients "$ovpn_status")
  sfos_count=$(count_connected_clients "$sfos_status")

  log "session-count:$phase openvpn=$ovpn_count sfos=$sfos_count"
}

emit_mismatch_log_file() {
  local category="$1"
  local log_file="$2"
  local total
  local remain

  [ -f "$log_file" ] || return 0

  total=$(wc -l < "$log_file")
  total=$(echo "$total" | tr -d '[:space:]')
  [ -n "$total" ] || total=0
  [ "$total" -gt 0 ] || return 0

  if [ "$SYNC_PTP_MISMATCH_MAX" -eq 0 ] || [ "$total" -le "$SYNC_PTP_MISMATCH_MAX" ]; then
    while IFS= read -r line; do
      [ -z "$line" ] && continue
      log "$line"
    done < "$log_file"
    return 0
  fi

  while IFS= read -r line; do
    [ -z "$line" ] && continue
    log "$line"
  done < <(head -n "$SYNC_PTP_MISMATCH_MAX" "$log_file")

  remain=$((total - SYNC_PTP_MISMATCH_MAX))
  log "dry-run mismatch:$category truncated shown=$SYNC_PTP_MISMATCH_MAX total=$total remaining=$remain"
}

compare_ccd_mismatch() {
  local label="$1"
  local expected_dir="$2"
  local current_dir="$3"
  local expected_list="$TMP_ROOT/${label}.ccd.expected"
  local current_list="$TMP_ROOT/${label}.ccd.current"
  local only_expected="$TMP_ROOT/${label}.ccd.only_expected"
  local only_current="$TMP_ROOT/${label}.ccd.only_current"
  local in_both="$TMP_ROOT/${label}.ccd.in_both"
  local missing_log="$TMP_ROOT/${label}.ccd.missing.log"
  local stale_log="$TMP_ROOT/${label}.ccd.stale.log"
  local diff_log="$TMP_ROOT/${label}.ccd.diff.log"
  local missing_count=0
  local stale_count=0
  local changed_count=0
  local f

  : > "$missing_log"
  : > "$stale_log"
  : > "$diff_log"

  find "$expected_dir" -maxdepth 1 -type f -printf '%f\n' | LC_ALL=C sort > "$expected_list"
  if [ -d "$current_dir" ]; then
    find "$current_dir" -maxdepth 1 -type f -printf '%f\n' | LC_ALL=C sort > "$current_list"
  else
    : > "$current_list"
  fi

  comm -23 "$expected_list" "$current_list" > "$only_expected"
  comm -13 "$expected_list" "$current_list" > "$only_current"
  comm -12 "$expected_list" "$current_list" > "$in_both"

  while IFS= read -r f; do
    [ -z "$f" ] && continue
    missing_count=$((missing_count + 1))
    echo "dry-run mismatch:$label ccd missing_in_current file=$f" >> "$missing_log"
  done < "$only_expected"

  while IFS= read -r f; do
    [ -z "$f" ] && continue
    stale_count=$((stale_count + 1))
    echo "dry-run mismatch:$label ccd stale_in_current file=$f" >> "$stale_log"
  done < "$only_current"

  while IFS= read -r f; do
    [ -z "$f" ] && continue
    if ! cmp -s "$expected_dir/$f" "$current_dir/$f"; then
      changed_count=$((changed_count + 1))
      echo "dry-run mismatch:$label ccd content_diff file=$f" >> "$diff_log"
    fi
  done < "$in_both"

  emit_mismatch_log_file "$label ccd missing_in_current" "$missing_log"
  emit_mismatch_log_file "$label ccd stale_in_current" "$stale_log"
  emit_mismatch_log_file "$label ccd content_diff" "$diff_log"

  DRY_RUN_MISMATCH_COUNT=$((DRY_RUN_MISMATCH_COUNT + missing_count + stale_count + changed_count))
  log "dry-run mismatch:$label ccd summary missing=$missing_count stale=$stale_count content_diff=$changed_count"
}

extract_route_set() {
  local conf="$1"
  if [ -f "$conf" ]; then
    awk '$1=="route"{print $2" "$3}' "$conf" | LC_ALL=C sort -u
  fi
}

compare_conf_route_mismatch() {
  local label="$1"
  local expected_conf="$2"
  local current_conf="$3"
  local expected_list="$TMP_ROOT/${label}.route.expected"
  local current_list="$TMP_ROOT/${label}.route.current"
  local only_expected="$TMP_ROOT/${label}.route.only_expected"
  local only_current="$TMP_ROOT/${label}.route.only_current"
  local missing_log="$TMP_ROOT/${label}.route.missing.log"
  local stale_log="$TMP_ROOT/${label}.route.stale.log"
  local add_count=0
  local remove_count=0
  local r

  : > "$missing_log"
  : > "$stale_log"

  extract_route_set "$expected_conf" > "$expected_list"
  extract_route_set "$current_conf" > "$current_list"

  comm -23 "$expected_list" "$current_list" > "$only_expected"
  comm -13 "$expected_list" "$current_list" > "$only_current"

  while IFS= read -r r; do
    [ -z "$r" ] && continue
    add_count=$((add_count + 1))
    echo "dry-run mismatch:$label route missing_in_current route='$r'" >> "$missing_log"
  done < "$only_expected"

  while IFS= read -r r; do
    [ -z "$r" ] && continue
    remove_count=$((remove_count + 1))
    echo "dry-run mismatch:$label route stale_in_current route='$r'" >> "$stale_log"
  done < "$only_current"

  emit_mismatch_log_file "$label route missing_in_current" "$missing_log"
  emit_mismatch_log_file "$label route stale_in_current" "$stale_log"

  DRY_RUN_MISMATCH_COUNT=$((DRY_RUN_MISMATCH_COUNT + add_count + remove_count))
  log "dry-run mismatch:$label route summary missing=$add_count stale=$remove_count"
}

compare_cert_mismatch() {
  local label="$1"
  local expected_dir="$2"
  local issued_dir="$PKI_DIR/pki/issued"
  local expected_list="$TMP_ROOT/${label}.cert.expected"
  local missing_log="$TMP_ROOT/${label}.cert.missing.log"
  local missing_count=0
  local ident

  : > "$missing_log"
  find "$expected_dir" -maxdepth 1 -type f -printf '%f\n' | LC_ALL=C sort -u > "$expected_list"

  while IFS= read -r ident; do
    [ -z "$ident" ] && continue
    if [ ! -f "$issued_dir/${ident}.crt" ]; then
      missing_count=$((missing_count + 1))
      echo "dry-run mismatch:$label cert missing_in_pki cert=${ident}.crt" >> "$missing_log"
    fi
  done < "$expected_list"

  emit_mismatch_log_file "$label cert missing_in_pki" "$missing_log"
  DRY_RUN_MISMATCH_COUNT=$((DRY_RUN_MISMATCH_COUNT + missing_count))
  log "dry-run mismatch:$label cert summary missing_in_pki=$missing_count"
}

if [[ "$REMOTE_NET" != */* ]]; then
  REMOTE_NET="${REMOTE_NET}/32"
fi

validate_non_negative_integer "SYNC_PTP_MISMATCH_MAX" "$SYNC_PTP_MISMATCH_MAX"
validate_binary_flag "SYNC_PTP_COMPARE_ROUTE" "$SYNC_PTP_COMPARE_ROUTE"
validate_binary_flag "SYNC_PTP_CLEANUP_STALE_ONLY" "$SYNC_PTP_CLEANUP_STALE_ONLY"
validate_binary_flag "SYNC_PTP_STALE_SAFE_MODE" "$SYNC_PTP_STALE_SAFE_MODE"

if [ "$SYNC_PTP_DRY_RUN" = "1" ] && [ "$SYNC_PTP_CLEANUP_STALE_ONLY" = "1" ]; then
  fail "--dry-run and --cleanup-stale-only cannot be used together"
fi

if [ "$SYNC_PTP_DRY_RUN" = "1" ] && [ "${#APPLY_CCD_CLI_LIST[@]}" -gt 0 ]; then
  fail "--dry-run and --apply-ccd cannot be used together"
fi

if [ "$SYNC_PTP_DRY_RUN" = "1" ] && [ -n "$SYNC_PTP_APPLY_CCD_LIST" ]; then
  fail "--dry-run and SYNC_PTP_APPLY_CCD_LIST cannot be used together"
fi

if [ "$SYNC_PTP_CLEANUP_STALE_ONLY" = "1" ] && { [ "${#APPLY_CCD_CLI_LIST[@]}" -gt 0 ] || [ -n "$SYNC_PTP_APPLY_CCD_LIST" ]; }; then
  fail "--cleanup-stale-only and --apply-ccd cannot be used together"
fi

CURRENT_STAGE="build-temp-ccd"
mkdir -p "$TMP_CCD_DIR" "$TMP_CCD_LEGACY_DIR" "$TMP_CCD_SFOS_DIR"

CURRENT_STAGE="query-db"
if ! SG_OUT=$(sudo -u postgres psql -d certsvc -At -F '|' -c "select c.hostname, l.assigned_ip from clients c join ip_leases l on l.client_id=c.id where c.vpn_type='openvpn' and coalesce(c.is_legacy,false)=false order by l.assigned_ip, c.id"); then
  fail "psql query failed for openvpn leases"
fi
if ! SG_LEGACY_OUT=$(sudo -u postgres psql -d certsvc -At -F '|' -c "select c.hostname, l.assigned_ip from clients c join ip_leases l on l.client_id=c.id where c.vpn_type='openvpn' and coalesce(c.is_legacy,false)=true order by l.assigned_ip, c.id"); then
  fail "psql query failed for openvpn legacy leases"
fi
if ! XGS_OUT=$(sudo -u postgres psql -d certsvc -At -F '|' -c "select c.hostname, l.assigned_ip from clients c join ip_leases l on l.client_id=c.id where c.vpn_type='sfos' order by l.assigned_ip, c.id"); then
  fail "psql query failed for sfos leases"
fi
mapfile -t SG_ROWS < <(printf '%s\n' "$SG_OUT")
mapfile -t SG_LEGACY_ROWS < <(printf '%s\n' "$SG_LEGACY_OUT")
mapfile -t XGS_ROWS < <(printf '%s\n' "$XGS_OUT")

CURRENT_STAGE="render-temp-ccd"
for row in "${SG_ROWS[@]}"; do
  [ -z "$row" ] && continue
  ident="${row%%|*}"
  ip="${row#*|}"
  ip="${ip%%/*}"
  cat > "${TMP_CCD_DIR}/${ident}" <<EOF
push-reset
push 'topology net30'
ifconfig-push ${ip} ${OPENVPN_TUN_SERIAL_IP}
push 'route-gateway ${OPENVPN_TUN_SERIAL_IP}'
push 'setenv-safe remote_network_1 ${REMOTE_NET}'
push 'setenv-safe local_network_1 ${ip}/32'
iroute ${ip} 255.255.255.255
EOF
done

CURRENT_STAGE="render-temp-ccd-sfos"
for row in "${XGS_ROWS[@]}"; do
  [ -z "$row" ] && continue
  ident="${row%%|*}"
  ip="${row#*|}"
  ip="${ip%%/*}"
  cat > "${TMP_CCD_SFOS_DIR}/${ident}" <<EOF
push-reset
push 'topology net30'
ifconfig-push ${ip} ${SFOS_TUN_SERIAL_IP}
push 'route-gateway ${SFOS_TUN_SERIAL_IP}'
push 'setenv-safe remote_network_1 ${REMOTE_NET}'
push 'setenv-safe local_network_1 ${ip}/32'
iroute ${ip} 255.255.255.255
EOF
done

CURRENT_STAGE="render-temp-ccd-legacy"
for row in "${SG_LEGACY_ROWS[@]}"; do
  [ -z "$row" ] && continue
  ident="${row%%|*}"
  ip="${row#*|}"
  ip="${ip%%/*}"
  cat > "${TMP_CCD_LEGACY_DIR}/${ident}" <<EOF
push-reset
push 'topology net30'
ifconfig-push ${ip} ${OPENVPN_LEGACY_TUN_SERIAL_IP}
push 'route-gateway ${OPENVPN_LEGACY_TUN_SERIAL_IP}'
push 'setenv-safe remote_network_1 ${REMOTE_NET}'
push 'setenv-safe local_network_1 ${ip}/32'
iroute ${ip} 255.255.255.255
EOF
done

if [ "$SYNC_PTP_CLEANUP_STALE_ONLY" = "1" ]; then
  CURRENT_STAGE="cleanup-stale-only"
  openvpn_stale_file="$TMP_ROOT/openvpn.stale.list"
  openvpn_legacy_stale_file="$TMP_ROOT/openvpn-legacy.stale.list"
  sfos_stale_file="$TMP_ROOT/sfos.stale.list"
  collect_stale_files "$TMP_CCD_DIR" "$CCD_DIR" "$openvpn_stale_file"
  collect_stale_files "$TMP_CCD_LEGACY_DIR" "$CCD_LEGACY_DIR" "$openvpn_legacy_stale_file"
  collect_stale_files "$TMP_CCD_SFOS_DIR" "$CCD_SFOS_DIR" "$sfos_stale_file"

  openvpn_removed=0
  openvpn_legacy_removed=0
  sfos_removed=0
  cleanup_stale_files "openvpn" "$CCD_DIR" "$openvpn_stale_file" openvpn_removed
  cleanup_stale_files "openvpn-legacy" "$CCD_LEGACY_DIR" "$openvpn_legacy_stale_file" openvpn_legacy_removed
  cleanup_stale_files "sfos" "$CCD_SFOS_DIR" "$sfos_stale_file" sfos_removed
  total_removed=$((openvpn_removed + openvpn_legacy_removed + sfos_removed))

  if [ "$total_removed" -gt 0 ]; then
    CURRENT_STAGE="cleanup-stale-reload"
    reload_or_restart_service "$OPENVPN_SERVICE" || fail "service apply failed after stale cleanup: $OPENVPN_SERVICE"
    reload_or_restart_service "$OPENVPN_LEGACY_SERVICE" || fail "service apply failed after stale cleanup: $OPENVPN_LEGACY_SERVICE"
    reload_or_restart_service "$SFOS_OPENVPN_SERVICE" || fail "service apply failed after stale cleanup: $SFOS_OPENVPN_SERVICE"
  fi

  log "cleanup-stale complete: removed_openvpn=$openvpn_removed removed_openvpn_legacy=$openvpn_legacy_removed removed_sfos=$sfos_removed total_removed=$total_removed"
  exit 0
fi

if [ "${#APPLY_CCD_CLI_LIST[@]}" -gt 0 ] || [ -n "$SYNC_PTP_APPLY_CCD_LIST" ]; then
  CURRENT_STAGE="apply-selected-ccd"
  apply_selected_ccd_files
  exit 0
fi

CURRENT_STAGE="render-temp-conf-openvpn"
{
  echo "port 1194"
  echo "proto udp"
  echo "dev tun-utm9"
  echo
  echo "server 10.242.254.0 255.255.255.0"
  echo "topology net30"
  echo
  echo "ccd-exclusive"
  echo "duplicate-cn"
  echo
  echo "route-gateway ${OPENVPN_TUN_SERIAL_IP}"
  echo "push 'route-gateway ${OPENVPN_TUN_SERIAL_IP}'"
  echo
  for row in "${SG_ROWS[@]}"; do
    [ -z "$row" ] && continue
    ip="${row#*|}"; ip="${ip%%/*}"
    echo "route ${ip} 255.255.255.255"
  done
  echo
  echo "ca ${PKI_DIR}/pki/ca.crt"
  echo "cert ${PKI_DIR}/pki/issued/server.crt"
  echo "key ${PKI_DIR}/pki/private/server.key"
  echo "dh ${PKI_DIR}/pki/dh.pem"
  echo
  echo "tls-auth ${PKI_DIR}/ta.key 0"
  echo
  echo "cipher AES-256-CBC"
  echo "auth SHA256"
  echo "data-ciphers AES-256-CBC"
  echo "data-ciphers-fallback AES-256-CBC"
  echo
  echo "client-config-dir ${CCD_DIR}"
  echo
  echo "keepalive 10 120"
  echo "persist-key"
  echo "persist-tun"
  echo
  echo "status /var/log/openvpn/status.log"
  echo "log-append /var/log/openvpn/openvpn.log"
  echo
  echo "verb 6"
} > "$TMP_OPENVPN_SERVER_CONF"

CURRENT_STAGE="render-temp-conf-sfos"
{
  echo "port 4443"
  echo "proto tcp"
  echo "dev tun-sfos"
  echo
  echo "server 10.242.255.0 255.255.255.0"
  echo "topology net30"
  echo
  echo "ccd-exclusive"
  echo "duplicate-cn"
  echo
  echo "route-gateway ${SFOS_TUN_SERIAL_IP}"
  echo "push 'route-gateway ${SFOS_TUN_SERIAL_IP}'"
  echo
  for row in "${XGS_ROWS[@]}"; do
    [ -z "$row" ] && continue
    ip="${row#*|}"; ip="${ip%%/*}"
    echo "route ${ip} 255.255.255.255"
  done
  echo
  echo "ca ${PKI_DIR}/pki/ca.crt"
  echo "cert ${PKI_DIR}/pki/issued/server.crt"
  echo "key ${PKI_DIR}/pki/private/server.key"
  echo "dh ${PKI_DIR}/pki/dh.pem"
  echo
  echo "cipher AES-128-CBC"
  echo "auth SHA256"
  echo
  echo "client-config-dir ${CCD_SFOS_DIR}"
  echo
  echo "keepalive 10 120"
  echo "persist-key"
  echo "persist-tun"
  echo "user nobody"
  echo "group nogroup"
  echo
  echo "status /var/log/openvpn/status-sfos.log"
  echo "log-append /var/log/openvpn/openvpn-sfos.log"
  echo
  echo "verb 6"
} > "$TMP_SFOS_SERVER_CONF"

CURRENT_STAGE="validate-temp-conf"
validate_conf_static "$TMP_OPENVPN_SERVER_CONF" "$CCD_DIR"
validate_conf_static "$TMP_SFOS_SERVER_CONF" "$CCD_SFOS_DIR"
validate_conf_with_openvpn "$TMP_OPENVPN_SERVER_CONF"
validate_conf_with_openvpn "$TMP_SFOS_SERVER_CONF"

if [ "$SYNC_PTP_DRY_RUN" = "1" ]; then
  CURRENT_STAGE="dry-run-compare"
  compare_ccd_mismatch "openvpn" "$TMP_CCD_DIR" "$CCD_DIR"
  compare_ccd_mismatch "openvpn-legacy" "$TMP_CCD_LEGACY_DIR" "$CCD_LEGACY_DIR"
  compare_ccd_mismatch "sfos" "$TMP_CCD_SFOS_DIR" "$CCD_SFOS_DIR"
  compare_cert_mismatch "openvpn" "$TMP_CCD_DIR"
  compare_cert_mismatch "openvpn-legacy" "$TMP_CCD_LEGACY_DIR"
  compare_cert_mismatch "sfos" "$TMP_CCD_SFOS_DIR"
  if [ "$SYNC_PTP_COMPARE_ROUTE" = "1" ]; then
    compare_conf_route_mismatch "openvpn" "$TMP_OPENVPN_SERVER_CONF" "$OPENVPN_SERVER_CONF"
    compare_conf_route_mismatch "sfos" "$TMP_SFOS_SERVER_CONF" "$SFOS_SERVER_CONF"
  else
    log "dry-run compare: route(conf) mismatch check skipped (enable with --compare-route)"
  fi
  log "dry-run enabled: validation passed; no production changes applied"
  log "dry-run summary: SG=${#SG_ROWS[@]} SG_LEGACY=${#SG_LEGACY_ROWS[@]} XGS=${#XGS_ROWS[@]}"
  log "dry-run mismatch total=$DRY_RUN_MISMATCH_COUNT"
  log "dry-run artifacts: $TMP_CCD_DIR $TMP_CCD_LEGACY_DIR $TMP_CCD_SFOS_DIR $TMP_OPENVPN_SERVER_CONF $TMP_SFOS_SERVER_CONF"
  exit 0
fi

CURRENT_STAGE="prepare-swap"
mkdir -p "$CCD_DIR" "$CCD_SFOS_DIR"

CCD_PARENT=$(dirname "$CCD_DIR")
CCD_SFOS_PARENT=$(dirname "$CCD_SFOS_DIR")
mkdir -p "$CCD_PARENT" "$CCD_SFOS_PARENT"

CCD_NEW_DIR=$(mktemp -d "${CCD_PARENT}/.ccd.new.XXXXXX")
CCD_SFOS_NEW_DIR=$(mktemp -d "${CCD_SFOS_PARENT}/.ccd-sfos.new.XXXXXX")
cp -a "$TMP_CCD_DIR/." "$CCD_NEW_DIR/"
cp -a "$TMP_CCD_SFOS_DIR/." "$CCD_SFOS_NEW_DIR/"

CURRENT_STAGE="swap-ccd"
CCD_OLD_DIR="${CCD_DIR}.old.${TS}.$$"
CCD_SFOS_OLD_DIR="${CCD_SFOS_DIR}.old.${TS}.$$"
mv "$CCD_DIR" "$CCD_OLD_DIR"
mv "$CCD_SFOS_DIR" "$CCD_SFOS_OLD_DIR"
mv "$CCD_NEW_DIR" "$CCD_DIR"
mv "$CCD_SFOS_NEW_DIR" "$CCD_SFOS_DIR"

CURRENT_STAGE="backup-and-apply-conf"
OPENVPN_CONF_BAK="${OPENVPN_SERVER_CONF}.bak.${TS}"
SFOS_CONF_BAK="${SFOS_SERVER_CONF}.bak.${TS}"
backup_conf_if_exists "$OPENVPN_SERVER_CONF" "$OPENVPN_CONF_BAK"
backup_conf_if_exists "$SFOS_SERVER_CONF" "$SFOS_CONF_BAK"
cp -a "$TMP_OPENVPN_SERVER_CONF" "$OPENVPN_SERVER_CONF"
cp -a "$TMP_SFOS_SERVER_CONF" "$SFOS_SERVER_CONF"

CURRENT_STAGE="pre-reload-snapshot"
log_client_count_snapshot "before"

CURRENT_STAGE="reload-openvpn"
if ! reload_or_restart_service "$OPENVPN_SERVICE"; then
  echo "ERROR: apply failed while reloading $OPENVPN_SERVICE, restoring previous config" >&2
  restore_on_failure || true
  exit 1
fi

CURRENT_STAGE="reload-sfos"
if ! reload_or_restart_service "$SFOS_OPENVPN_SERVICE"; then
  echo "ERROR: apply failed while reloading $SFOS_OPENVPN_SERVICE, restoring previous config" >&2
  restore_on_failure || true
  exit 1
fi

CURRENT_STAGE="post-reload-snapshot"
log_client_count_snapshot "after"

CURRENT_STAGE="cleanup-old-ccd"
rm -rf "$CCD_OLD_DIR" "$CCD_SFOS_OLD_DIR"

CURRENT_STAGE="done"
echo "sync complete: SG=${#SG_ROWS[@]} XGS=${#XGS_ROWS[@]}"
