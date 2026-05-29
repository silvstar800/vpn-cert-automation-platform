#!/bin/bash
set -euo pipefail

###############################################################################
# certsvc-apc-offline.sh
#
# Offline SFOS (Fortinet) enrollment - works without web/DB connectivity
# Queues APC request for later processing
#
# Usage:
#   certsvc-apc-offline.sh [OPTIONS]
#   --hostname HOSTNAME    Client hostname (required)
#   --ip IP_ADDRESS        Assign specific IP (optional, auto-allocate if omitted)
#   --mac MAC_ADDRESS      MAC address (optional)
###############################################################################

set -o pipefail

# Configuration
OFFLINE_QUEUE_DIR="${OFFLINE_QUEUE_DIR:-/opt/certsvc/offline-queue}"
CERTSVC_DIR="${CERTSVC_DIR:-/opt/certsvc}"
VENV_BIN="${CERTSVC_DIR}/venv/bin"

# Offline IP range for SFOS (must match offline_queue_manager.py)
OFFLINE_SFOS_SUBNET="172.23.223.0/25"

# Input variables
HOSTNAME=""
ASSIGNED_IP=""

# Helper functions
validate_hostname() {
  local v="$1"
  [[ "$v" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || {
    echo "❌ 호스트명 형식이 올바르지 않습니다." >&2
    echo "   허용: 영문자, 숫자, 마침표(.), 하이픈(-), 밑줄(_)  /  최대 128자" >&2
    exit 1
  }
}

validate_ip() {
  local ip="$1"
  local subnet="$2"
  
  # Basic IP format check
  if ! [[ "$ip" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
    echo "❌ IP 주소 형식이 올바르지 않습니다: $ip" >&2
    exit 1
  fi
  
  # Verify IP is in subnet
  python3 << PYEOF
import ipaddress
try:
    ip = ipaddress.ip_address("$ip")
    subnet = ipaddress.ip_network("$subnet", strict=False)
    if ip not in subnet:
        print(f"❌ IP {ip}는 대역 {subnet}에 포함되지 않습니다.", flush=True)
        exit(1)
    if str(ip) == str(subnet.network_address) or str(ip) == str(subnet.broadcast_address):
        print(f"❌ 네트워크/브로드캐스트 주소는 사용할 수 없습니다: {ip}", flush=True)
        exit(1)
except Exception as e:
    print(f"❌ IP 검증 오류: {e}", flush=True)
    exit(1)
PYEOF
  [ $? -eq 0 ] || exit 1
}

auto_allocate_ip() {
  local subnet="$1"
  local exclude_ips="$2"
  
  python3 << PYEOF
import ipaddress
import sys

subnet = ipaddress.ip_network("$subnet", strict=False)
exclude = set("$exclude_ips".split(",")) if "$exclude_ips" else set()

for ip in subnet.hosts():
    if str(ip) not in exclude:
        print(str(ip))
        sys.exit(0)

print("❌ 오프라인 대역에 사용 가능한 IP가 없습니다.", file=sys.stderr)
sys.exit(1)
PYEOF
}

get_offline_used_ips() {
  local subnet="$1"
  
  # Query Python to get used IPs from offline queue
  python3 << PYEOF
import json
import ipaddress
from pathlib import Path

queue_dir = Path("$OFFLINE_QUEUE_DIR")
subnet = ipaddress.ip_network("$subnet", strict=False)
used_ips = set()

if queue_dir.exists():
    for qfile in queue_dir.glob("*.json"):
        try:
            with open(qfile) as f:
                data = json.load(f)
                ip = data.get("assigned_ip")
                if ip:
                    used_ips.add(ip)
        except:
            pass

print(",".join(sorted(used_ips)))
PYEOF
}

queue_apc_request() {
  local hostname="$1"
  local assigned_ip="$2"
  
  # Call Python to queue request using venv python
  "${VENV_BIN}/python3" << PYEOF
import sys
sys.path.insert(0, "$CERTSVC_DIR")

from managers.offline_queue_manager import OfflineQueueManager

mgr = OfflineQueueManager(queue_dir="$OFFLINE_QUEUE_DIR")

try:
    filepath = mgr.save_apc_request(
        hostname="$hostname",
        region="sfos",
        assigned_ip="$assigned_ip",
    )
    print(f"✓ 오프라인 큐에 저장됨: {filepath}")
except Exception as e:
    print(f"❌ 큐 저장 실패: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
}

print_help() {
  cat <<'EOF'
사용법:
  certsvc-apc-offline --hostname HOSTNAME [--ip IP]

설명:
  - SFOS 오프라인 APC 전용 스크립트입니다.
  - SG / SG-legacy 용도가 아닙니다.
  - 기본 대역 기준은 172.23.220.0/22 이고, 오프라인 예약 풀은 뒤쪽 /25(172.23.223.0/25)입니다.

옵션:
  --hostname HOSTNAME   필수, 장비 호스트명
  --ip IP               선택, 수동 IP 지정
  -h, --help             도움말 출력
EOF
}

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      print_help
      exit 0
      ;;
    --hostname)
      HOSTNAME="$2"
      shift 2
      ;;
    --ip)
      ASSIGNED_IP="$2"
      shift 2
      ;;
    *)
      echo "❌ 알 수 없는 옵션: $1" >&2
      exit 1
      ;;
  esac
done

# Validate inputs
if [ -z "$HOSTNAME" ]; then
  echo "❌ --hostname은 필수입니다." >&2
  exit 1
fi

validate_hostname "$HOSTNAME"

# Auto-allocate IP if not specified
if [ -z "$ASSIGNED_IP" ]; then
  echo "📍 IP 주소 자동 할당 중..."
  USED_IPS=$(get_offline_used_ips "$OFFLINE_SFOS_SUBNET")
  ASSIGNED_IP=$(auto_allocate_ip "$OFFLINE_SFOS_SUBNET" "$USED_IPS") || exit 1
  echo "✓ 할당 IP: $ASSIGNED_IP"
else
  validate_ip "$ASSIGNED_IP" "$OFFLINE_SFOS_SUBNET"
fi

# Queue APC request
echo "📝 오프라인 큐에 APC 요청 저장 중..."
queue_apc_request "$HOSTNAME" "$ASSIGNED_IP"

echo ""
echo "✅ 오프라인 APC 요청 완료!"
echo ""
echo "   호스트명: $HOSTNAME"
echo "   할당 IP: $ASSIGNED_IP"
echo ""
echo "💡 Web/DB 복구 후 다음 명령으로 서비스를 재시작하세요:"
echo "   sudo systemctl restart certsvc nginx"
echo ""
