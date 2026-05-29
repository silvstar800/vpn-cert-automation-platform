#!/bin/bash
set -euo pipefail

###############################################################################
# certsvc-apc-request.sh
#
# APC (SFOS 장비) 등록 요청 클라이언트 - 인터랙티브 입력 방식
#
# certsvc-apc-request: 관리자 모드 (/admin/apc/request, 비밀번호 입력)
###############################################################################

SERVER_URL="${SERVER_URL:-https://localhost:8443}"

# ------------------------------------------------------------------------------
# 서버 패턴과 동일한 입력 검증
# hostname : ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$
# serial   : ^[A-Za-z0-9][A-Za-z0-9._-]{3,63}$  (최소 4자)
# model    : ^[A-Za-z0-9][A-Za-z0-9 ._()/+-]{0,127}$
# ------------------------------------------------------------------------------
validate_hostname() {
  local v="$1"
  [[ "$v" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || {
    echo "오류: 호스트명에 허용되지 않는 문자가 포함되어 있습니다." >&2
    echo "      허용: 영문자, 숫자, 마침표(.), 하이픈(-), 밑줄(_)  /  첫 글자: 영문자 또는 숫자" >&2
    exit 1
  }
}

validate_serial() {
  local v="$1"
  [[ "$v" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{3,63}$ ]] || {
    echo "오류: 시리얼 번호 형식이 올바르지 않습니다." >&2
    echo "      허용: 영문자, 숫자, 마침표(.), 하이픈(-), 밑줄(_)  /  최소 4자, 최대 64자" >&2
    exit 1
  }
}

validate_model() {
  local v="$1"
  [[ "$v" =~ ^[A-Za-z0-9][A-Za-z0-9\ ._()/+-]{0,127}$ ]] || {
    echo "오류: 장비 모델명 형식이 올바르지 않습니다." >&2
    echo "      허용: 영문자, 숫자, 공백, 마침표(.), 괄호, 하이픈(-), 밑줄(_), 슬래시(/), +" >&2
    exit 1
  }
}

# ------------------------------------------------------------------------------
# 인터랙티브 입력
# ------------------------------------------------------------------------------
prompt_required() {
  local prompt="$1"
  local value=""
  while [ -z "$value" ]; do
    read -r -p "$prompt" value
    [ -z "$value" ] && echo "  → 필수 항목입니다. 다시 입력해 주세요." >&2
  done
  printf '%s' "$value"
}

prompt_secret() {
  local prompt="$1"
  local value=""
  while [ -z "$value" ]; do
    read -r -s -p "$prompt" value
    echo >&2
    [ -z "$value" ] && echo "  → 필수 항목입니다. 다시 입력해 주세요." >&2
  done
  printf '%s' "$value"
}

collect_inputs() {
  echo >&2
  echo "=== APC 관리자 등록 ===" >&2
  echo "서버: $SERVER_URL" >&2
  echo >&2

  HOSTNAME=$(prompt_required "장비 호스트명: ")
  validate_hostname "$HOSTNAME"

  SERIAL_NUMBER=$(prompt_required "시리얼 번호: ")
  validate_serial "$SERIAL_NUMBER"

  DEVICE_MODEL=$(prompt_required "장비 모델명 (예: SG135): ")
  validate_model "$DEVICE_MODEL"

  ADMIN_PASSWORD=$(prompt_secret "장비 관리자 비밀번호: ")
  [ -n "$ADMIN_PASSWORD" ] || { echo "오류: 비밀번호가 비어 있습니다." >&2; exit 1; }

  echo >&2
  echo "--- 입력 확인 ---" >&2
  echo "  호스트명 : $HOSTNAME" >&2
  echo "  시리얼   : $SERIAL_NUMBER" >&2
  echo "  모델명   : $DEVICE_MODEL" >&2
  echo "  비밀번호 : (입력됨)" >&2
  echo >&2
  read -r -p "위 정보로 등록 요청을 전송하시겠습니까? [y/N] " confirm
  echo >&2
  [[ "$confirm" =~ ^[Yy]$ ]] || { echo "취소되었습니다." >&2; exit 0; }
}

# ------------------------------------------------------------------------------
# API 요청
# ------------------------------------------------------------------------------
apc_admin() {
  local payload url outfile response http_code body

  payload=$(printf \
    '{"hostname":"%s","serialNumber":"%s","deviceModel":"%s","adminPassword":"%s","mac":""}' \
    "$HOSTNAME" "$SERIAL_NUMBER" "$DEVICE_MODEL" "$ADMIN_PASSWORD")

  url="${SERVER_URL%/}/admin/apc/request"
  outfile="${HOSTNAME}.apc"

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 요청 전송 → $url" >&2

  response=$(curl -sSk -w '\n%{http_code}' \
    -X POST \
    -H "Content-Type: application/json" \
    -d "$payload" \
    "$url")

  http_code=$(printf '%s' "$response" | tail -n1)
  body=$(printf '%s' "$response" | head -n -1)

  if [ "$http_code" != "200" ]; then
    echo "오류: 서버 응답 $http_code" >&2
    echo "$body" >&2
    exit 1
  fi

  printf '%s' "$body" > "$outfile"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] APC 파일 저장 완료: $(realpath "$outfile")" >&2
}

# ------------------------------------------------------------------------------
main() {
  if [[ "$(basename "$0")" == *enroll* ]]; then
    echo "오류: 자동등록(certsvc-apc-enroll)은 비활성화되었습니다." >&2
    echo "      certsvc-apc-request 명령을 사용하세요." >&2
    exit 1
  fi

  if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    cat <<'EOF'
사용법:
  certsvc-apc-request

환경변수:
  SERVER_URL    서버 주소 (기본값: https://localhost:8443)

실행하면 호스트명, 시리얼 번호, 모델명, 장비 비밀번호를 순서대로 입력하는 프롬프트가 나타납니다.
EOF
    exit 0
  fi

  collect_inputs
  apc_admin
}

main "$@"
