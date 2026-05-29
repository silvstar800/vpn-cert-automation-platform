#!/bin/bash
set -euo pipefail

SERVER_IP="__SERVER_IP__"
SERVER_API_PORT="__SERVER_API_PORT__"
VPN_PORT="__VPN_PORT__"
IPT_FILE="__IPT_FILE__"
SERVER_API_PORTS_RAW="${SERVER_API_PORTS:-$SERVER_API_PORT}"

declare -a SERVER_API_PORTS=()
declare -a RULE_PERSIST_TCP=()
RULE_PERSIST_UDP=""

validate_config() {
  local PLACEHOLDER_SERVER_IP="__SERVER""_IP__"
  local PLACEHOLDER_SERVER_API_PORT="__SERVER""_API_PORT__"
  local PLACEHOLDER_VPN_PORT="__VPN""_PORT__"
  case "${SERVER_IP}:${SERVER_API_PORT}:${VPN_PORT}" in
    *"${PLACEHOLDER_SERVER_IP}"*|*"${PLACEHOLDER_SERVER_API_PORT}"*|*"${PLACEHOLDER_VPN_PORT}"*)
      log "[ERROR] certsvc-ensure-fw.sh contains unresolved placeholders"
      return 1
      ;;
  esac
}

assert_no_placeholders() {
  local missing=0
  local PLACEHOLDER_SERVER_IP="__SERVER""_IP__"
  local PLACEHOLDER_SERVER_API_PORT="__SERVER""_API_PORT__"
  local PLACEHOLDER_VPN_PORT="__VPN""_PORT__"
  local PLACEHOLDER_IPT_FILE="__IPT""_FILE__"

  for placeholder in "$PLACEHOLDER_SERVER_IP" "$PLACEHOLDER_SERVER_API_PORT" "$PLACEHOLDER_VPN_PORT" "$PLACEHOLDER_IPT_FILE"; do
    if grep -qF "$placeholder" "$0"; then
      log "placeholder not rendered: $placeholder"
      missing=1
    fi
  done

  if [ "$missing" -ne 0 ]; then
    log "aborting before iptables execution because template placeholders remain"
    return 1
  fi
}

RULE_PERSIST_UDP="-A OUTPUT -p udp -d ${SERVER_IP} --dport ${VPN_PORT} -j ACCEPT"
IPT_ANCHOR1='-A OUTPUT -m confirmed ! -d 224.0.0.0/4 -j ACCEPT'
IPT_ANCHOR2='-A OUTPUT -o lo -j ACCEPT'

MODE="full"

usage() {
  echo "Usage: $0 [--runtime-only|--full]"
}

log() {
  printf '[%s] [certsvc-ensure-fw] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2
}

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --runtime-only)
        MODE="runtime-only"
        ;;
      --full)
        MODE="full"
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
}

init_api_ports() {
  local normalized token
  normalized="$(printf '%s' "$SERVER_API_PORTS_RAW" | tr ',;' '  ')"

  for token in $normalized; do
    if ! [[ "$token" =~ ^[0-9]+$ ]]; then
      continue
    fi
    if [ "${#SERVER_API_PORTS[@]}" -gt 0 ]; then
      if [[ " ${SERVER_API_PORTS[*]} " =~ " ${token} " ]]; then
        continue
      fi
    fi
    SERVER_API_PORTS+=("$token")
  done

  if [ "${#SERVER_API_PORTS[@]}" -eq 0 ]; then
    if [[ "$SERVER_API_PORT" =~ ^[0-9]+$ ]]; then
      SERVER_API_PORTS=("$SERVER_API_PORT")
    else
      log "invalid SERVER_API_PORT: $SERVER_API_PORT"
      return 1
    fi
  fi
}

build_rules() {
  local api_port
  RULE_PERSIST_UDP="-A OUTPUT -p udp -d ${SERVER_IP} --dport ${VPN_PORT} -j ACCEPT"
  RULE_PERSIST_TCP=()
  for api_port in "${SERVER_API_PORTS[@]}"; do
    RULE_PERSIST_TCP+=("-A OUTPUT -p tcp -d ${SERVER_IP} --dport ${api_port} -j ACCEPT")
  done
}

ensure_runtime_rule_at() {
  local chain="$1"
  local pos="$2"
  shift 2
  iptables -C "$chain" "$@" 2>/dev/null && return 0
  iptables -I "$chain" "$pos" "$@" 2>/dev/null || true
}

apply_runtime_rules() {
  local pos=2
  local api_port

  ensure_runtime_rule_at OUTPUT 1 -p udp -d "$SERVER_IP" --dport "$VPN_PORT" -j ACCEPT
  for api_port in "${SERVER_API_PORTS[@]}"; do
    ensure_runtime_rule_at OUTPUT "$pos" -p tcp -d "$SERVER_IP" --dport "$api_port" -j ACCEPT
    pos=$((pos + 1))
  done
}

needs_persistent_patch() {
  local file_path="$1"
  local udp_count tcp_count
  local rule
  local placeholder_regex="__SERVER_IP__|__SERVER_API_PORT__|__VPN_PORT__"

  if grep -qE -- "$placeholder_regex" "$file_path"; then
    return 0
  fi

  udp_count="$(awk -v r="$RULE_PERSIST_UDP" '($0==r){c++} END{print c+0}' "$file_path")"
  if [ "$udp_count" -ne 1 ]; then
    return 0
  fi

  for rule in "${RULE_PERSIST_TCP[@]}"; do
    tcp_count="$(awk -v r="$rule" '($0==r){c++} END{print c+0}' "$file_path")"
    if [ "$tcp_count" -ne 1 ]; then
      return 0
    fi
  done

  return 1
}

validate_candidate() {
  local file_path="$1"
  local rule

  [ -s "$file_path" ] || {
    log "validation failed: candidate is empty"
    return 1
  }
  grep -q '^\*filter' "$file_path" || {
    log "validation failed: missing *filter header"
    return 1
  }
  grep -q '^:OUTPUT ' "$file_path" || {
    log "validation failed: missing OUTPUT chain definition"
    return 1
  }
  grep -q '^COMMIT$' "$file_path" || {
    log "validation failed: missing COMMIT"
    return 1
  }
  grep -qxF -- "$RULE_PERSIST_UDP" "$file_path" || {
    log "validation failed: missing required UDP rule"
    return 1
  }
  for rule in "${RULE_PERSIST_TCP[@]}"; do
    grep -qxF -- "$rule" "$file_path" || {
      log "validation failed: missing required TCP rule: $rule"
      return 1
    }
  done
}

line_is_desired_rule() {
  local line="$1"
  local rule
  if [ "$line" = "$RULE_PERSIST_UDP" ]; then
    return 0
  fi
  for rule in "${RULE_PERSIST_TCP[@]}"; do
    if [ "$line" = "$rule" ]; then
      return 0
    fi
  done
  return 1
}

write_candidate_without_rules() {
  local src="$1"
  local dst="$2"
  local line
  local placeholder_regex='__SERVER_IP__|__SERVER_API_PORT__|__VPN_PORT__'

  : > "$dst"
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" =~ $placeholder_regex ]]; then
      continue
    fi
    if line_is_desired_rule "$line"; then
      continue
    fi
    printf '%s\n' "$line" >> "$dst"
  done < "$src"
}

insert_rules_with_anchor() {
  local src="$1"
  local dst="$2"
  local rule_block="$3"
  local anchor=""

  anchor="$IPT_ANCHOR1"
  grep -qxF -- "$anchor" "$src" || anchor="$IPT_ANCHOR2"

  if grep -qxF -- "$anchor" "$src"; then
    awk -v a="$anchor" -v block="$rule_block" '
BEGIN{inserted=0}
{
  if ($0==a && inserted==0) {
    printf "%s", block
    inserted=1
  }
  print
}
END{
  if (inserted==0) {
    printf "%s", block
  }
}
' "$src" > "$dst"
    return 0
  fi

  if grep -q '^COMMIT$' "$src"; then
    awk -v block="$rule_block" '
BEGIN{inserted=0}
{
  if ($0=="COMMIT" && inserted==0) {
    printf "%s", block
    inserted=1
  }
  print
}
END{
  if (inserted==0) {
    printf "%s", block
  }
}
' "$src" > "$dst"
    return 0
  fi

  cat "$src" > "$dst"
  printf '%s' "$rule_block" >> "$dst"
}

patch_persistent_rules() {
  local file_path="$1"
  local file_dir file_base ts backup_file tmp_candidate="" tmp_final=""
  local rule_block

  [ -f "$file_path" ] || {
    log "persistent file not found, skip: $file_path"
    return 0
  }

  if ! needs_persistent_patch "$file_path"; then
    log "persistent rules already correct, no file update"
    return 0
  fi

  file_dir="$(dirname "$file_path")"
  file_base="$(basename "$file_path")"
  ts="$(date '+%Y%m%d%H%M%S')"
  backup_file="${file_dir}/${file_base}.bak.${ts}"

  tmp_candidate="$(mktemp "${file_dir}/.${file_base}.candidate.XXXXXX")"
  tmp_final="$(mktemp "${file_dir}/.${file_base}.final.XXXXXX")"
  trap 'rm -f "${tmp_candidate:-}" "${tmp_final:-}"' RETURN

  write_candidate_without_rules "$file_path" "$tmp_candidate"

  rule_block="$(printf "%s\n" "$RULE_PERSIST_UDP" "${RULE_PERSIST_TCP[@]}")"
  rule_block+=$'\n'
  insert_rules_with_anchor "$tmp_candidate" "$tmp_final" "$rule_block"

  if ! validate_candidate "$tmp_final"; then
    log "persistent patch aborted: validation failed"
    return 1
  fi

  cp -p "$file_path" "$backup_file"
  mv -f "$tmp_final" "$file_path"
  rm -f "$tmp_candidate"
  trap - RETURN
  log "persistent rules updated safely, backup: $backup_file"
}

main() {
  parse_args "$@"
  validate_config || exit 1
  assert_no_placeholders
  init_api_ports || exit 1
  build_rules
  apply_runtime_rules
  if [ "$MODE" = "full" ]; then
    patch_persistent_rules "$IPT_FILE" || true
  fi
}

main "$@"
