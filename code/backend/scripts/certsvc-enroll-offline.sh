#!/bin/bash
set -euo pipefail

###############################################################################
# certsvc-enroll-offline.sh
#
# Offline OpenVPN enrollment - works without web/DB connectivity
# Creates certificates and queues enrollment request for later processing
#
# Usage:
#   certsvc-enroll-offline.sh [OPTIONS]
#   --hostname HOSTNAME    Client hostname (required)
#   --region [sg|legacy]   VPN region (default: sg)
#   --ip IP_ADDRESS        Assign specific IP (optional, auto-allocate if omitted)
#   --mac MAC_ADDRESS      MAC address (optional)
###############################################################################

set -o pipefail

# Configuration
PKI_DIR="${PKI_DIR:-/etc/openvpn/pki}"
OFFLINE_QUEUE_DIR="${OFFLINE_QUEUE_DIR:-/opt/certsvc/offline-queue}"
CERTSVC_DIR="${CERTSVC_DIR:-/opt/certsvc}"
VENV_BIN="${CERTSVC_DIR}/venv/bin"

# Offline IP ranges (must match offline_queue_manager.py)
OFFLINE_SG_SUBNET="172.23.211.0/25"
OFFLINE_LEGACY_SUBNET="172.23.215.0/25"
OFFLINE_SFOS_SUBNET="172.23.223.0/25"

# OpenVPN standard ranges (reserved, don't use for offline)
OPENVPN_SG_SUBNET="172.23.208.0/24"
OPENVPN_SG_GW="172.23.208.1"
OPENVPN_LEGACY_SUBNET="172.23.212.0/24"
OPENVPN_LEGACY_GW="172.23.212.1"
OPENVPN_SFOS_SUBNET="172.23.220.0/24"
OPENVPN_SFOS_GW="172.23.220.1"

# Input variables
HOSTNAME=""
REGION=""
ASSIGNED_IP=""
IS_LEGACY=false

# Helper functions
validate_hostname() {
  local v="$1"
  [[ "$v" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || {
    echo "❌ 호스트명 형식이 올바르지 않습니다." >&2
    echo "   허용: 영문자, 숫자, 마침표(.), 하이픈(-), 밑줄(_)  /  최대 128자" >&2
    exit 1
  }
}

validate_region() {
  local v="$1"
  case "$v" in
    sg|legacy|sfos)
      ;;
    *)
      echo "❌ 지역은 sg, legacy, sfos 중 하나여야 합니다." >&2
      exit 1
      ;;
  esac
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

ensure_certificates() {
  local hostname="$1"
  local ca_cert="$PKI_DIR/ca.crt"
  local cert_file="$PKI_DIR/issued/${hostname}.crt"
  local key_file="$PKI_DIR/private/${hostname}.key"
  
  # Check if certs already exist
  if [ -f "$cert_file" ] && [ -f "$key_file" ]; then
    echo "✓ 기존 인증서 사용: $hostname"
    return 0
  fi
  
  # Generate using EasyRSA
  echo "🔐 인증서 생성 중: $hostname"
  
  cd "$PKI_DIR"
  
  # Import EasyRSA functions
  if [ ! -f "easyrsa" ]; then
    echo "❌ EasyRSA not found at $PKI_DIR/easyrsa" >&2
    exit 1
  fi
  
  # Generate certificate request
  ./easyrsa --batch gen-req "$hostname" nopass > /dev/null 2>&1 || {
    echo "❌ CSR 생성 실패: $hostname" >&2
    exit 1
  }
  
  # Sign certificate
  ./easyrsa --batch sign-req client "$hostname" > /dev/null 2>&1 || {
    echo "❌ 인증서 서명 실패: $hostname" >&2
    exit 1
  }
  
  if [ ! -f "$cert_file" ] || [ ! -f "$key_file" ]; then
    echo "❌ 인증서 생성 실패" >&2
    exit 1
  fi
  
  echo "✓ 인증서 생성 완료: $hostname"
}

queue_enroll_request() {
  local hostname="$1"
  local region="$2"
  local assigned_ip="$3"
  local is_legacy="$4"
  local py_is_legacy="False"
  if [[ "$is_legacy" == true ]]; then
    py_is_legacy="True"
  fi
  
  local ca_cert="$PKI_DIR/ca.crt"
  local cert_file="$PKI_DIR/issued/${hostname}.crt"
  local key_file="$PKI_DIR/private/${hostname}.key"
  
  # Read certificate files
  local ca_content key_content cert_content
  ca_content=$(cat "$ca_cert") || { echo "❌ CA 인증서 읽기 실패" >&2; exit 1; }
  cert_content=$(cat "$cert_file") || { echo "❌ 인증서 읽기 실패" >&2; exit 1; }
  key_content=$(cat "$key_file") || { echo "❌ 개인키 읽기 실패" >&2; exit 1; }
  
  # Call Python to queue request using venv python
  "${VENV_BIN}/python3" << PYEOF
import sys
sys.path.insert(0, "$CERTSVC_DIR")

from managers.offline_queue_manager import OfflineQueueManager

mgr = OfflineQueueManager(queue_dir="$OFFLINE_QUEUE_DIR")

try:
    filepath = mgr.save_enroll_request(
        hostname="$hostname",
        region="$region",
        assigned_ip="$assigned_ip",
        ca_cert=r"""$ca_content""",
        certificate=r"""$cert_content""",
        key=r"""$key_content""",
        is_legacy=${py_is_legacy},
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
  certsvc-enroll-offline --hostname HOSTNAME --region sg|legacy [--ip IP]

설명:
  - SG 오프라인 enroll: --region sg
  - SG-legacy 오프라인 enroll: --region legacy
  - --region은 필수 입력입니다 (기본값 없음).
  - openssl version 2>/dev/null 결과가 0.* / 1.0.0* / 1.0.1* 이면 --region legacy 를 사용한다.
  - 그 외 버전이면 --region sg 를 사용한다.
  - 내부적으로는 detect_vpn_port()가 openssl 버전을 보고 VPN 포트를 선택한다.

대역 기준:
  - sg     : SG 일반 대역용 오프라인 enroll
             운영 대역 기준은 172.23.208.0/22, 오프라인 예약 풀은 뒤쪽 /25(172.23.211.0/25)
  - legacy : SG-legacy 대역용 오프라인 enroll
             운영 대역 기준은 172.23.212.0/22, 오프라인 예약 풀은 뒤쪽 /25(172.23.215.0/25)

옵션:
  --hostname HOSTNAME   필수, 장비 호스트명
  --region sg|legacy    필수, SG 또는 SG-legacy 구분
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
    --region)
      REGION="$2"
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

if [ -z "$REGION" ]; then
  echo "❌ --region은 필수입니다 (sg 또는 legacy)." >&2
  exit 1
fi

validate_region "$REGION"

# Determine subnet based on region
case "$REGION" in
  sg)
    SUBNET="$OFFLINE_SG_SUBNET"
    IS_LEGACY=false
    ;;
  legacy)
    SUBNET="$OFFLINE_LEGACY_SUBNET"
    IS_LEGACY=true
    ;;
  sfos)
    SUBNET="$OFFLINE_SFOS_SUBNET"
    IS_LEGACY=false
    ;;
esac

# Auto-allocate IP if not specified
if [ -z "$ASSIGNED_IP" ]; then
  echo "📍 IP 주소 자동 할당 중..."
  USED_IPS=$(get_offline_used_ips "$SUBNET")
  ASSIGNED_IP=$(auto_allocate_ip "$SUBNET" "$USED_IPS") || exit 1
  echo "✓ 할당 IP: $ASSIGNED_IP"
else
  validate_ip "$ASSIGNED_IP" "$SUBNET"
fi

# Generate/ensure certificates
ensure_certificates "$HOSTNAME"

# Queue enrollment request
echo "📝 오프라인 큐에 등록 요청 저장 중..."
queue_enroll_request "$HOSTNAME" "$REGION" "$ASSIGNED_IP" "$IS_LEGACY"

echo ""
echo "✅ 오프라인 등록 완료!"
echo ""
echo "   호스트명: $HOSTNAME"
echo "   지역: $REGION"
echo "   할당 IP: $ASSIGNED_IP"
echo "   MAC: ${MAC_ADDRESS:-없음}"
echo ""
echo "💡 Web/DB 복구 후 다음 명령으로 서비스를 재시작하세요:"
echo "   sudo systemctl restart certsvc nginx"
echo ""
