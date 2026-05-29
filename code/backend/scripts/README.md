# 유틸리티 스크립트 관리 가이드

이 디렉토리는 SSL 인증서 관리 서비스(`certsvc`)의 모든 유틸리티 및 마이그레이션 스크립트를 중앙에서 관리합니다.

---

## 📑 목차
- [데이터베이스 초기화 & 마이그레이션](#1-데이터베이스-초기화--마이그레이션)
- [자산 관리 스크립트](#2-자산-관리-스크립트)
- [VPN 설정 동기화](#3-vpn-설정-동기화)
- [운영 점검 스크립트](#4-운영-점검-스크립트)
- [스크립트 사용 예제](#5-스크립트-사용-예제)

---

## 1. 데이터베이스 초기화 & 마이그레이션

### 📄 `init_db.py`
**목적**: 데이터베이스 스키마 초기화 및 테이블 생성

**주요 기능**:
- SQLAlchemy ORM 모델 기반 테이블 자동 생성
- 고유 제약조건(UNIQUE CONSTRAINT) 추가
  - `clients` 테이블: hostname + vpn_type 유니크 제약
  - `ip_leases` 테이블: vpn_type + identity 유니크 제약
- IP 주소 데이터 타입 변환 (TEXT → INET)

**사용법**:
```bash
python3 scripts/init_db.py
```

**실행 조건**:
- PostgreSQL 데이터베이스 연결 필수
- `.env` 파일에서 `DATABASE_URL` 설정 필수

---

### 📄 `migrate_credentials_hashes.py`
**목적**: 레거시 비밀번호 해시를 새로운 형식으로 마이그레이션

**주요 기능**:
- 기존 평문 또는 구형 해시 비밀번호를 SHA256 형식으로 변환
- 해시 형식: `sha256$<hex_digest>`
- 이미 마이그레이션된 항목은 그대로 유지 (중복 마이그레이션 방지)

**사용법**:
```bash
python3 scripts/migrate_credentials_hashes.py
```

**변경 내용**:
- 출력: 마이그레이션된 레코드 개수

---

### 📄 `migrate_default_asset_status.py`
**목적**: 장비 자산의 기본 상태를 변경하는 마이그레이션

**주요 기능**:
- 상태 코드 1 (임대) → 상태 코드 3 (신규 재고) 변경
- 클라이언트가 등록되지 않은 자산만 대상
- 자동 생성 자산의 초기 상태 보정
- 히스토리 기록 자동 생성

**사용법**:
```bash
python3 scripts/migrate_default_asset_status.py
```

**변경 기준**:
- 현재 상태: 1 (임대)
- 클라이언트 ID: NULL
- 고객명: 공백

---

## 2. 자산 관리 스크립트

### 📄 `import_equipment_assets.py`
**목적**: Excel 파일(XLSX)에서 장비 자산 정보를 데이터베이스로 임포트

**주요 기능**:
- XLSX 파일의 첫 번째 시트에서 자산 정보 읽기
- 공유 문자열(shared strings) 처리로 정확한 셀 값 추출
- 대량 자산 등록 및 이력 관리

**사용법**:
```bash
python3 scripts/import_equipment_assets.py <경로/파일명.xlsx>
```

**필수 환경**:
- PostgreSQL 데이터베이스 연결
- `.env` 파일 설정

**Excel 형식**:
- 시트: 첫 번째 시트만 처리
- 칼럼: 시리얼, 모델, 상태 등 정의된 스키마 준수

---

### 📄 `fix_enrolled_equipment_asset.py`
**목적**: 등록된 장비 자산의 메타데이터를 수정

**주요 기능**:
- 특정 시리얼 번호의 장비 자산 조회
- 장비 모델, 상태, 판매 유형 등 속성 수정
- 수정 이력 자동 기록

**사용법**:
```bash
python3 scripts/fix_enrolled_equipment_asset.py
```

**주의사항**:
- 스크립트 내부에 `serial` 변수로 대상 시리얼 번호 지정
- 필요시 추출 후 커맨드라인 인자로 변경 권장

**예제**:
```python
# 스크립트 내 수정:
serial = "S180A0232B9B1E5"
expected_model = "SG 105"
```

---

### 📄 `fix_legacy_client_ip.py`
**목적**: 클라이언트를 레거시 VPN으로 마이그레이션

**주요 기능**:
- 클라이언트를 `is_legacy=True`로 표시
- 기존 IP 리스 삭제
- 레거시 VPN IP 풀에서 새 IP 할당
- 레거시 CCD(client config directory) 구성

**사용법**:
```bash
python3 scripts/fix_legacy_client_ip.py
```

**주의사항**:
- 스크립트 내부에 `hostname` 변수로 대상 호스트명 지정
- 레거시 VPN 설정이 완료되어 있어야 함

**예제**:
```python
# 스크립트 내 수정:
hostname = "miso-1004-clinic"
```

---

## 3. VPN 설정 동기화

### 📄 `sync_ptp.sh`
**목적**: OpenVPN Point-to-Point 설정의 클라이언트 설정 파일(CCD) 동기화

**주요 기능**:
- 데이터베이스에서 VPN 클라이언트의 할당된 IP 조회
- OpenVPN CCD 디렉토리를 자동으로 재구성
- 두 가지 VPN 유형 지원:
  - Standard OpenVPN (CCD_DIR)
  - SFOS OpenVPN (CCD_SFOS_DIR)
- 정책 기반 라우팅 구성 자동화

**사용법**:
```bash
bash scripts/sync_ptp.sh
```

**환경 변수** (`.env`에서 읽음):
```
CCD_DIR=/etc/openvpn/ccd
CCD_SFOS_DIR=/etc/openvpn/ccd-sfos
OPENVPN_SERVER_CONF=/etc/openvpn/server/server.conf
SFOS_SERVER_CONF=/etc/openvpn/server/server-sfos.conf
OPENVPN_TUN_SERIAL_IP=10.242.254.1
SFOS_TUN_SERIAL_IP=10.242.255.1
OPENVPN_PUSH_REMOTE_NETWORK_1=10.0.200.4
```

**생성되는 파일 예**:
```
/etc/openvpn/ccd/client-hostname
├─ push-reset
├─ topology net30
├─ ifconfig-push 172.23.210.10 10.242.254.1
├─ push 'route-gateway 10.242.254.1'
├─ iroute 172.23.210.10 255.255.255.255
```

---

### 📄 `sync_ptp_vpn.py`
**목적**: Python 기반 VPN PTP 설정 동기화

**주요 기능**:
- `sync_ptp.sh`의 Python 구현 버전
- 데이터베이스 쿼리로 VPN 클라이언트 정보 조회
- IP 주소 할당 및 라우팅 규칙 설정
- 트래버샬 네트워크 계산 (CIDR 기반)

**사용법**:
```bash
python3 scripts/sync_ptp_vpn.py
```

**역할**:
- IP CIDR에서 호스트 경로 생성
- CCD 디렉토리 구성 파일 자동 생성
- 기본 경로 정보(gateway, netmask) 자동 삽입

---

### 📄 `sync_vpn_cfg.py`
**목적**: VPN 클라이언트의 클래식 설정 동기화

**주요 기능**:
- 클라이언트별 CCD(client config directory) 파일 생성
- 기존 설정 파일 관리 및 정리
- 표준 OpenVPN 및 SFOS OpenVPN 동시 지원
- IP 리스 기반 설정 자동화

**사용법**:
```bash
python3 scripts/sync_vpn_cfg.py
```

**생성 과정**:
1. 데이터베이스에서 활성 클라이언트 조회
2. 할당된 IP 주소 확인
3. CCD 파일 생성/업데이트
4. 불필요한 설정 파일 삭제

---

## 4. 운영 점검 스크립트

### 📄 ops_smoke_check.py
운영 점검 명령의 상세 설명과 실행 기준은 [CommandList.md](../CommandList.md#L99) 를 본다.

**사용법**:

`python3 scripts/ops_smoke_check.py` 는 실행 예시이며, 실제 운영 기준과 관련 명령은 [CommandList.md](../CommandList.md#L99) 를 본다.

**활용 시점**:
- 배포 직후
- 서비스 재시작 직후
- 장애 조치 후 기본 검증

---

## 5. 스크립트 사용 예제

### 시나리오 1: 새로운 certsvc 배포

```bash
# 1. 데이터베이스 초기화
python3 scripts/init_db.py

# 2. 기존 비밀번호 해시 마이그레이션 (필요시)
python3 scripts/migrate_credentials_hashes.py

# 3. 자산 정보 일괄 임포트 (필요시)
python3 scripts/import_equipment_assets.py /path/to/assets.xlsx

# 4. VPN 설정 동기화
bash scripts/sync_ptp.sh
python3 scripts/sync_vpn_cfg.py
```

### 시나리오 2: 특정 클라이언트 레거시 VPN 마이그레이션

```bash
# 1. 스크립트 내 hostname 수정
# scripts/fix_legacy_client_ip.py 파일 열기 후 hostname 변수 변경

# 2. 실행
python3 scripts/fix_legacy_client_ip.py

# 3. VPN 설정 재동기화
bash scripts/sync_ptp.sh
```

### 시나리오 3: 자산 상태 대량 보정

```bash
# 1. 마이그레이션 실행 (상태 1 → 3 변경)
python3 scripts/migrate_default_asset_status.py

# 2. 결과 출력: 변경된 레코드 개수
```

### 시나리오 4: 특정 장비 정보 수정

```bash
# 1. 스크립트 내 serial, expected_model 수정
# scripts/fix_enrolled_equipment_asset.py 파일 편집

# 2. 실행
python3 scripts/fix_enrolled_equipment_asset.py
```

---

## 5. 파이썬 스크립트 실행 환경

모든 Python 스크립트는 다음 환경에서 실행됩니다:

**가상환경**:
```bash
source /opt/certsvc/venv/bin/activate
```

**의존성** (requirements 내 포함):
```
sqlalchemy
psycopg2-binary (또는 psycopg)
pydantic
```

**환경 변수**:
- `DATABASE_URL`: PostgreSQL 연결 문자열 (`.env`에서 로드)
- 다른 설정값들도 `.env`에서 자동 로드

---

## 6. 주의사항 및 팁

### 권장사항
✅ 프로덕션 환경에서는 스크립트 실행 전 데이터베이스 백업
✅ 마이그레이션 스크립트는 테스트 환경에서 먼저 검증
✅ 스크립트 실행 후 로그 및 히스토리 기록 확인

### 문제 해결
- **DB 연결 오류**: `.env`의 `DATABASE_URL` 확인
- **권한 오류**: `sudo` 권한 또는 psql 사용자 권한 확인
- **Python 모듈 오류**: 가상환경 활성화 및 의존성 설치 확인

---

## 7. 스크립트 버전 히스토리

| 스크립트 | 마지막 수정 | 상태 |
|---------|-----------|------|
| init_db.py | Mar 30, 2026 | ✅ 안정 |
| migrate_credentials_hashes.py | Apr 2, 2026 | ✅ 안정 |
| migrate_default_asset_status.py | Apr 15, 2026 | ✅ 안정 |
| import_equipment_assets.py | Apr 15, 2026 | ✅ 안정 |
| fix_enrolled_equipment_asset.py | Apr 15, 2026 | ✅ 안정 |
| fix_legacy_client_ip.py | Apr 16, 2026 | ✅ 안정 |
| sync_ptp.sh | Apr 3, 2026 | ✅ 안정 |
| sync_ptp_vpn.py | Apr 10, 2026 | ✅ 안정 |
| sync_vpn_cfg.py | Apr 10, 2026 | ✅ 안정 |

---

**마지막 업데이트**: 2026년 4월 17일
