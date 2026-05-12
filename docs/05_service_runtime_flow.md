# CertSvc 운영 서비스 · API · 동작 상세 정리

## 1. 문서 목적

이 문서는 `certsvc` 운영 환경을 처음 인수받은 사람이 다음 내용을 **코드 기준으로 이해**할 수 있도록 정리한 기준 문서다.

- 어떤 서비스가 떠 있는지
- 각 서비스가 어떤 포트를 사용하는지
- 어떤 API가 어떤 화면/장비/스크립트에서 호출되는지
- 각 API가 내부적으로 어떤 파일/DB/서비스를 건드리는지
- SG / Legacy SG / SFOS(XGS) 흐름이 어떻게 다른지
- 백업/복구/보안 모니터가 실제로 어떻게 동작하는지

이 문서는 요약본이 아니라 **운영 구조 원본**으로 사용한다.  
Word 운영문서와 PPT는 이 문서를 바탕으로 재구성하는 것을 기준으로 한다.

---

## 2. 전체 서비스 구성

### 2.1 핵심 구성 요소

`certsvc` 운영 환경은 크게 아래 7개 영역으로 구성된다.

1. `nginx`
2. `certsvc` FastAPI 백엔드
3. `PostgreSQL`
4. `OpenVPN SG`
5. `OpenVPN Legacy SG`
6. `OpenVPN SFOS/XGS`
7. `Runtime Guard`

### 2.2 서비스별 역할 / 포트 / 확인 위치

| 서비스 | 역할 | 포트 | 확인 명령 | 주요 로그/파일 |
|---|---|---|---|---|
| `nginx` | 외부 HTTPS 진입점, 정적 UI 제공, FastAPI reverse proxy | `443/tcp` 외부, 내부 프록시 대상 `8443/tcp` | `systemctl status nginx` | `/var/log/nginx/access.log`, `/var/log/nginx/error.log` |
| `certsvc` | FastAPI 백엔드, 등록/API/보안모니터/백업 처리 | `8443/tcp` | `systemctl status certsvc` | `journalctl -u certsvc` |
| `postgresql` | 클라이언트/임대/백업/장비 데이터 저장 | `5432/tcp` | `systemctl status postgresql` | PostgreSQL 로그, `DATABASE_URL` |
| `openvpn-server@server.service` | 일반 SG용 OpenVPN 서버 | `1194/udp` | `systemctl status openvpn-server@server.service` | `/run/openvpn-server/status-server.log`, `/etc/openvpn/server/server.conf` |
| `openvpn-server@server-legacy.service` | 레거시 SG용 OpenVPN 서버 | `1195/udp` | `systemctl status openvpn-server@server-legacy.service` | `/var/log/openvpn/status-legacy.log`, `/etc/openvpn/server/server-legacy.conf` |
| `openvpn-server@server-sfos.service` | SFOS / XGS용 OpenVPN 서버 | `4443/tcp` | `systemctl status openvpn-server@server-sfos.service` | `/run/openvpn-server/status-server-sfos.log`, `/etc/openvpn/server/server-sfos.conf` |
| `certsvc_runtime_guard.service` / `timer` | 재부팅 후 서비스/인터페이스/방화벽 상태 보정 | 별도 listen 포트 없음 | `systemctl status certsvc_runtime_guard.service`, `systemctl status certsvc_runtime_guard.timer` | `/var/log/certsvc-runtime-guard.log` |

### 2.3 실제 통신 경로

#### 운영자 웹 접속

1. 운영자가 브라우저로 `https://<service-domain>` 접속
2. `nginx:443` 가 정적 프론트(`ui/dist`)를 응답
3. 프론트는 같은 origin 기준으로 API 호출
4. `nginx` 가 API 요청을 `127.0.0.1:8443` 의 `certsvc` 로 프록시
5. `certsvc` 는 DB / 파일 / OpenVPN 상태 파일 / 백업 경로를 참조해 응답

#### SG 장비 등록

1. 장비가 `vpn_enroll.sh` 스크립트 다운로드
2. 장비가 `/enroll` 호출
3. `certsvc` 가 HMAC 검증, DB 반영, IP 할당, CCD 작성, 인증서 번들 생성
4. 장비는 `tar.gz` 번들을 내려받아 적용
5. 장비는 이후 OpenVPN `1194/udp` 또는 `1195/udp` 로 서버 접속

#### SFOS/XGS 등록

1. 운영 UI 또는 내부 장비가 APC 생성을 요청
2. `certsvc` 가 DB/credential/CCD/APC payload 생성
3. 장비는 `.apc` 파일을 적용
4. 장비는 `4443/tcp` 로 OpenVPN 접속

---

## 3. 서비스별 실제 역할

### 3.1 nginx

역할:

- 외부 웹 진입점
- React 빌드 산출물 제공
- FastAPI reverse proxy
- 현재 운영에서는 HTTPS/TLS 적용 상태

운영 관점에서 nginx가 처리하는 것:

- `/` 이하 프론트 라우팅
- UI가 호출하는 API를 동일 origin 에서 백엔드로 전달
- 외부 노출 포트 통합

점검 포인트:

- 도메인 접속이 안 되면 `nginx` 상태 먼저 확인
- 브라우저가 뜨는데 API만 실패하면 프록시/백엔드 둘 다 확인

### 3.2 certsvc (FastAPI)

역할:

- 웹 로그인 세션 처리
- 장비/클라이언트 목록 제공
- SG 등록 처리
- SFOS APC 생성
- IP 임대 조회
- 장비/라이선스 관리
- 보안 모니터
- 백업/복구
- Slack 알림
- Runtime Guard 로그/API 제공

`certsvc` 가 직접 참조하는 주요 리소스:

- PostgreSQL
- PKI 디렉터리
- CCD 디렉터리
- OpenVPN status 파일
- 백업 디렉터리
- 복구 staging 디렉터리
- feature log 디렉터리
- security state 파일

### 3.3 PostgreSQL

역할:

- 웹이 보여주는 운영 데이터의 기준 저장소
- 클라이언트 / credential / lease / backup setting / 장비 자산 저장

### 3.4 OpenVPN 3종

#### 일반 SG
- 서비스: `openvpn-server@server.service`
- 포트: `1194/udp`
- 대역: `172.23.208.0/22`
- gateway/serial IP: `10.242.254.1`
- CCD: `/etc/openvpn/ccd`

#### Legacy SG
- 서비스: `openvpn-server@server-legacy.service`
- 포트: `1195/udp`
- 대역: `172.23.212.0/22`
- gateway/serial IP: `10.242.253.1`
- CCD: `/etc/openvpn/ccd-legacy`

#### SFOS/XGS
- 서비스: `openvpn-server@server-sfos.service`
- 포트: `4443/tcp`
- 대역: `172.23.220.0/22`
- gateway/serial IP: `10.242.255.1`
- CCD: `/etc/openvpn/ccd-sfos`

### 3.5 Runtime Guard

역할:

- 서버 부팅 후 서비스 상태 보정
- OpenVPN 인터페이스 등장 확인
- firewalld 규칙 적용
- certsvc/nginx/postgresql 상태 보장
- 필요 시 `sync_ptp_vpn.py` 실행

즉, 운영자가 수동으로 다 챙기지 않아도 최소한의 기동 상태를 자동으로 맞춰주는 보조 서비스다.

---

## 4. 파일/디렉터리 구조

### 4.1 애플리케이션 경로

| 경로 | 의미 |
|---|---|
| `/opt/certsvc/app.py` | FastAPI 메인 앱 |
| `/opt/certsvc/models.py` | DB 모델 정의 |
| `/opt/certsvc/db.py` | DB 엔진/세션 |
| `/opt/certsvc/managers/` | manager 계층 |
| `/opt/certsvc/templates/vpn_enroll.sh` | SG 등록 스크립트 템플릿 |
| `/opt/certsvc/ui/dist` | 배포용 프론트 빌드 산출물 |
| `/opt/certsvc/.env` | 런타임 환경 변수 |

### 4.2 OpenVPN 관련 경로

| 경로 | 의미 |
|---|---|
| `/etc/openvpn/pki` | 인증서/키/ta.key |
| `/etc/openvpn/ccd` | SG CCD |
| `/etc/openvpn/ccd-legacy` | Legacy SG CCD |
| `/etc/openvpn/ccd-sfos` | SFOS CCD |
| `/etc/openvpn/server/server.conf` | 일반 SG 서버 설정 |
| `/etc/openvpn/server/server-legacy.conf` | Legacy 서버 설정 |
| `/etc/openvpn/server/server-sfos.conf` | SFOS 서버 설정 |
| `/run/openvpn-server/status-server.log` | 일반 SG 상태 파일 |
| `/var/log/openvpn/status-legacy.log` | Legacy 상태 파일 |
| `/run/openvpn-server/status-server-sfos.log` | SFOS 상태 파일 |

### 4.3 백업/보안 관련 경로

| 경로 | 의미 |
|---|---|
| `/opt/certsvc/backups` | 백업 번들 루트 |
| `/opt/certsvc/restore_staging` | 복구 검증 staging |
| `/opt/certsvc/restore_points` | 복구 전 restore point |
| `/opt/certsvc/state/security_monitor.json` | 보안 모니터 상태 파일 |
| `/var/log/certsvc-runtime-guard.log` | runtime guard 로그 |
| `/opt/certsvc/feature_logs` | 기능별 JSON 로그 |

---

## 5. 데이터베이스 구조

### 5.1 핵심 테이블

#### `clients`

용도:

- 등록된 VPN 클라이언트의 기준 정보 저장

핵심 컬럼:

- `hostname`
- `mac`
- `vpn_type`
- `cert_cn`
- `is_legacy`
- `status`
- `created_at`
- `updated_at`

#### `credentials`

용도:

- SFOS/XGS APC 생성에 필요한 계정 정보 저장

핵심 컬럼:

- `client_id`
- `username`
- `password`
- `sfos_admin_password_enc`

#### `ip_leases`

용도:

- 클라이언트와 VPN IP 연결

핵심 컬럼:

- `client_id`
- `assigned_ip`
- `is_active`

#### `backup_settings`

용도:

- FTP 백업 설정
- 스케줄
- Slack 알림 설정
- 백업/알림 로그

#### `equipment_assets`

용도:

- 장비 시리얼/모델/라이선스/상태 관리

#### `equipment_asset_history`

용도:

- 장비 자산 변경 이력 관리

---

## 6. 인증/접근 제어 구조

### 6.1 접근 방식 종류

`certsvc` 는 API를 전부 동일한 방식으로 열어두지 않는다. 크게 5가지 접근 방식이 있다.

| 방식 | 설명 | 대표 API |
|---|---|---|
| 공개/기본 | 추가 인증 없이 호출 가능 | `/health` |
| 웹 세션 또는 내부 접근 | 운영 UI 로그인 세션 또는 내부 토큰/대역 허용 | `/clients`, `/leases`, `/equipment-assets`, `/system/status`, `/admin/apc/request` |
| 내부 전용 | 내부 대역 또는 `X-Internal-Token` 필요 | `/enroll/apc`, `/feature-logs/{feature}` |
| 내부 또는 enroll token | 내부 접근 또는 `X-Enroll-Token` / query `token` 허용 | `/enroll/vpn_enroll.sh` |
| 보안 콘솔 전용 | 웹 로그인 세션 또는 내부 토큰 후 보안 콘솔 진입 | `/security/*`, `/backup/*`, `/alerts/history`, `/runtime-guard/logs` |

### 6.2 실제 인증 요소

- 웹 로그인 세션 쿠키
- `X-Internal-Token`
- `X-Enroll-Token`
- `X-Inventory-Sync-Token`
- Debug Mode 비밀번호

### 6.3 보안 콘솔

`/security/console/access`

동작:

1. 먼저 웹 로그인 또는 내부 접근이 가능해야 함
2. 관리자 비밀번호(`DEBUG_MODE_PASSWORD`) 확인
3. 프론트는 세션 기준으로 보안 콘솔 진입 상태 유지

---

## 7. API 전체 구조

## 7.1 인증/세션 API

| Method | Path | 접근 | 호출 주체 | 역할 |
|---|---|---|---|---|
| `GET` | `/auth/me` | 없음(세션 없으면 401) | 웹 프론트 | 현재 로그인 상태 확인 |
| `POST` | `/auth/login` | 없음 | 웹 프론트 | 웹 콘솔 로그인, 세션 쿠키 발급 |
| `POST` | `/auth/logout` | 세션 기반 | 웹 프론트 | 세션 쿠키 삭제 |

### 7.2 상태/자산/클라이언트 API

| Method | Path | 접근 | 호출 주체 | 역할 |
|---|---|---|---|---|
| `GET` | `/health` | 공개 | 운영자, 헬스체크 | DB/디렉터리 기본 상태 반환 |
| `GET` | `/equipment-assets` | 웹세션 또는 내부 | 웹 프론트 | 장비/라이선스 목록 |
| `POST` | `/equipment-assets/manual` | 웹세션 또는 내부 | 웹 프론트 | 장비 수동 등록 |
| `GET` | `/equipment-assets/import/template` | 웹세션 또는 내부 | 웹 프론트 | 엑셀 템플릿 다운로드 |
| `POST` | `/equipment-assets/import` | 웹세션 또는 내부 | 웹 프론트 | 엑셀 일괄 등록 |
| `POST` | `/equipment-assets/sync/callback` | inventory token 또는 내부 | 외부 시스템 | 고객사/자산 동기화 콜백 |
| `POST` | `/equipment-assets/sync/trigger` | 웹세션 또는 내부 | 웹 프론트 | 외부 자산 동기화 수동 트리거 |
| `GET` | `/clients` | 웹세션 또는 내부 | 웹 프론트 | 클라이언트 목록 + 실시간 연결 상태 |
| `GET` | `/clients/{client_id}/backup` | 웹세션 또는 내부 | 웹 프론트 | 특정 클라이언트 최근 백업 메타데이터 |
| `GET` | `/clients/{client_id}/backup/download` | 웹세션 또는 내부 | 웹 프론트 | 클라이언트 최근 백업 다운로드 |
| `GET` | `/leases` | 웹세션 또는 내부 | 웹 프론트 | IP lease 목록 |
| `GET` | `/leases/stats` | 웹세션 또는 내부 | 웹 프론트 | SG/Legacy/SFOS 대역별 통계 |
| `GET` | `/system/status` | 웹세션 또는 내부 | 웹 프론트 | 서비스 상태 + 자원 상태 |

### 7.3 백업/복구 API

| Method | Path | 접근 | 호출 주체 | 역할 |
|---|---|---|---|---|
| `GET` | `/runtime-guard/logs` | 보안 콘솔 | 웹 프론트 | runtime guard 로그 |
| `GET` | `/feature-logs/{feature}` | 내부 전용 | 운영 점검 | 기능별 로그 확인 |
| `GET` | `/alerts/history` | 보안 콘솔 | 웹 프론트 | 알림 이력 |
| `GET` | `/backup/settings` | 보안 콘솔 | 웹 프론트 | 백업 설정/Slack 설정/로그 조회 |
| `GET` | `/backup/logs` | 보안 콘솔 | 웹 프론트 | 백업 로그 조회 |
| `GET` | `/backup/status` | 보안 콘솔 | 웹 프론트 | 최근 백업 상태 요약 |
| `POST` | `/backup/settings/test` | 보안 콘솔 | 웹 프론트 | FTP 연결 테스트 |
| `POST` | `/backup/settings` | 보안 콘솔 | 웹 프론트 | 백업 설정 저장 |
| `POST` | `/backup/run` | 보안 콘솔 | 웹 프론트 | 즉시 백업 실행 |
| `POST` | `/backup/restore/list` | 보안 콘솔 | 웹 프론트 | FTP 원격 경로의 복구 가능한 백업 조회 |
| `POST` | `/backup/restore/run` | 보안 콘솔 | 웹 프론트 | validate 또는 실제 restore 실행 |
| `POST` | `/backup/slack/settings` | 보안 콘솔 | 웹 프론트 | Slack 알림 설정 저장 |
| `POST` | `/backup/client/upload` | 서명 검증 | SG 클라이언트 | 장비 백업 파일 업로드 |
| `POST` | `/backup/slack/test` | 보안 콘솔 | 웹 프론트 | Slack 테스트 메시지 발송 |

### 7.4 보안 모니터 API

| Method | Path | 접근 | 호출 주체 | 역할 |
|---|---|---|---|---|
| `GET` | `/security/monitor` | 보안 콘솔 | 웹 프론트 | 보안 모니터 상태 조회 |
| `POST` | `/security/console/access` | 웹세션 또는 내부 | 웹 프론트 | Debug Mode 진입 |
| `POST` | `/security/monitor/settings` | 보안 콘솔 | 웹 프론트 | alert/ban/window 기준 변경 |
| `POST` | `/security/monitor/unban` | 보안 콘솔 | 웹 프론트 | 수동 차단 해제 |
| `POST` | `/security/monitor/unenroll` | 보안 콘솔 | 웹 프론트 | 등록된 클라이언트 정보 삭제/해제 |

### 7.5 등록/배포 API

| Method | Path | 접근 | 호출 주체 | 역할 |
|---|---|---|---|---|
| `GET` | `/enroll/vpn_enroll.sh` | 내부 또는 enroll token | SG 장비 / 운영자 | SG 등록 스크립트 다운로드 |
| `POST` | `/enroll` | 서명 검증 + 보안 모니터 | SG / Legacy 장비 | SG 등록 후 `tar.gz` 반환 |
| `POST` | `/enroll/apc` | 내부 전용 + 서명 검증 | SFOS/XGS 장비 | APC payload 생성 |
| `POST` | `/admin/apc/request` | 웹세션 또는 내부 | 운영자 UI | 관리자 수동 APC 생성 |

---

## 8. API별 실제 동작

## 8.1 `/health`

확인 항목:

- DB 연결 성공 여부
- `PKI_DIR`
- `CCD_DIR`
- `CCD_SFOS_DIR`
- `ui/dist/index.html`
- `BACKUP_BASE_DIR`

즉, 단순 프로세스 헬스가 아니라 **운영에 필요한 기본 파일 구조까지 포함한 건강상태**를 반환한다.

## 8.2 `/clients`

실제 동작:

1. `clients` 테이블 조회
2. OpenVPN status 파일 3개 읽기
3. `ip_leases` 와 조합해 `assignedIp` 계산
4. `equipment_assets` 와 조합해 `serialNumber` 연결
5. 각 클라이언트의 연결 상태를 `active / disconnected / inactive` 로 계산
6. 인증서 만료일 계산

즉, `/clients` 는 DB만 보는 API가 아니라
**DB + status 파일 + 장비 정보**를 합쳐서 화면용 목록을 만든다.

## 8.3 `/leases`

실제 동작:

1. `clients` 목록 조회
2. status 파일을 읽어 현재 연결 여부 계산
3. `ip_leases` 와 매핑
4. `openvpn`, `openvpn-legacy`, `sfos` 타입별로 상태 구분

## 8.4 `/system/status`

실제 동작:

1. DB `SELECT 1`
2. `systemctl is-active` 로 OpenVPN 서비스 상태 확인
3. status 파일에서 실제 연결된 클라이언트 수 확인
4. 자원 사용량 수집
5. 디렉터리 존재 여부 확인

여기서 중요한 점:

- 서비스가 `active` 라도 연결된 client 가 0이면 `disconnected`
- DB 연결 실패 시 `certsvc-db` 는 `stopped`

즉, systemd 상태만 보지 않고 **실제 연결성**까지 반영한다.

## 8.5 `/enroll`

SG / Legacy 장비 등록 핵심 API.

처리 순서:

1. hostname / mac / serial / model normalize
2. timestamp 검증 (`ENROLL_TS_DRIFT_SECONDS`)
3. HMAC 서명 검증 (`ENROLL_SECRET`)
4. `selectedVpnPort` 로 일반 SG / Legacy SG 판정
5. `clients` upsert
6. 해당 대역에서 IP 할당
7. 인증서/키 생성 또는 재사용
8. OpenVPN bundle (`tar.gz`) 생성
9. `ip_leases` 동기화
10. 장비 시리얼이 있으면 `equipment_assets` 반영
11. CCD 파일 작성
12. feature log 기록
13. 최종적으로 `FileResponse(.tar.gz)` 반환

## 8.6 `/enroll/apc`

SFOS/XGS 장비가 내부 등록 시 호출하는 APC 생성 API.

처리 순서:

1. 내부 접근인지 확인
2. timestamp 검증
3. hostname / mac / serial / model normalize
4. SFOS용 서명 검증
5. `clients(vpn_type=sfos)` upsert
6. SFOS 대역에서 IP 할당
7. `ccd-sfos` 에 CCD 작성
8. credential 생성
9. 인증서/키 로드
10. `.apc` JSON payload 생성
11. `application/octet-stream` 으로 반환

## 8.7 `/admin/apc/request`

운영자가 웹에서 수동으로 APC 생성 시 사용.

차이점:

- 웹 로그인 또는 내부 접근 기반
- `adminPassword` 필수
- 운영자가 자산 목록에서 시리얼과 모델을 보고 APC 생성 가능

## 8.8 `/backup/client/upload`

장비가 자체 백업 파일을 서버에 업로드하는 API.

처리 순서:

1. timestamp 검증
2. HMAC 검증
3. hostname 검증
4. replay 방지 캐시 검사
5. `keyId` 비교
6. 장비 유형에 따라 저장 경로 결정
   - `sg`
   - `sg_legacy`
   - `xgs`
7. 파일 저장
8. 25MB 초과 시 차단
9. 오래된 이전 백업 prune
10. feature log 기록

## 8.9 `/security/monitor`

보안 모니터에서 보여주는 데이터는 다음을 기반으로 생성된다.

- `security_monitor.json`
- backup settings 의 Slack 설정
- 보안 알림/차단 집계

즉, 보안 콘솔은 별도 인증 후 **상태 파일 기반**으로 동작한다.

---

## 9. 실제 동작 흐름

## 9.1 운영자 웹 로그인 흐름

1. 브라우저가 `/auth/me` 호출
2. 세션이 없으면 로그인 페이지 표시
3. `/auth/login` 으로 ID/PW 전송
4. `certsvc` 가 로그인 rate limit / password 검증
5. 서명된 세션 쿠키 발급
6. 이후 UI는 같은 origin 기준으로 API 호출

## 9.2 SG 등록 흐름

1. 운영자 또는 장비가 `/enroll/vpn_enroll.sh` 다운로드
2. 스크립트가 장비 hostname, serial, mac 를 수집
3. 스크립트가 `/enroll` 호출
4. 백엔드가 등록 처리 후 `.tar.gz` 반환
5. 장비가 인증서, key, client.conf, helper script 설치
6. 장비가 `1194/udp` 또는 `1195/udp` 로 서버 접속

## 9.3 Legacy SG 차이

Legacy SG 는 일반 SG 와 거의 같지만 아래가 다르다.

- 포트: `1195/udp`
- CCD 디렉터리: `ccd-legacy`
- 대역: `172.23.212.0/22`
- gateway: `10.242.253.1`
- 상태 파일: `status-legacy.log`

## 9.4 SFOS / XGS APC 흐름

1. 장비 또는 운영자가 APC 생성 요청
2. `certsvc` 가 DB/credential/CCD 정보 생성
3. `.apc` payload 반환
4. 장비가 `.apc` import
5. 장비가 `4443/tcp` 로 OpenVPN 접속

## 9.5 백업/복구 흐름

### 백업

1. 운영자가 백업 설정 저장
2. 필요 시 FTP 연결 테스트
3. 수동 또는 스케줄러가 `run_backup_job()` 실행
4. 로컬 번들/manifest 생성
5. FTP 원격 경로 업로드
6. backup log 저장
7. Slack 알림 필요 시 발송

### 복구

1. 운영자가 FTP 원격 경로에서 백업 목록 조회
2. 선택한 backupId 로 restore list/run 수행
3. bundle 다운로드
4. staging 검증
5. validate 모드면 검증 결과만 반환
6. restore 모드면 restore point 생성 후 실제 복구 수행
7. 로그/Slack 기록

## 9.6 보안 모니터 흐름

1. `/enroll` 실패 중 일부는 보안 실패로 집계
2. threshold 초과 시 alert / ban 처리
3. 보안 모니터 화면에서 banned / observed 대상 확인
4. 운영자가 수동 unban 또는 unenroll 수행
5. Slack 알림과 연동될 수 있음

---

## 10. 로그와 확인 포인트

### 10.1 가장 먼저 볼 로그

| 상황 | 우선 로그 |
|---|---|
| 웹 API 전체 오류 | `journalctl -u certsvc` |
| 웹 접속 오류 | `nginx access/error.log` |
| SG 연결 상태 이상 | `/run/openvpn-server/status-server.log` |
| Legacy 상태 이상 | `/var/log/openvpn/status-legacy.log` |
| SFOS 상태 이상 | `/run/openvpn-server/status-server-sfos.log` |
| 재부팅 후 이상 | `/var/log/certsvc-runtime-guard.log` |
| 기능별 상세 추적 | `feature_logs` 또는 `/feature-logs/{feature}` |

### 10.2 기능 로그 종류

현재 feature log 는 아래 분류를 사용한다.

- `inventory-sync`
- `enroll`
- `apc`
- `admin-apc`
- `auth`
- `security`
- `backup`

---

## 11. 운영자가 알아야 하는 핵심 포인트

1. **웹 화면에 보이는 상태는 DB만으로 결정되지 않는다.**
   - OpenVPN status 파일과 조합해서 계산된다.

2. **SG / Legacy / SFOS 는 서로 다른 서비스/포트/대역/CCD 를 사용한다.**
   - 문제 분석 시 항상 유형부터 구분해야 한다.

3. **`/health` 는 파일 구조까지 포함한 상태 체크다.**
   - 서비스 up 이라고 건강한 것이 아니다.

4. **보안 모니터 API와 백업 API는 일반 UI보다 더 강한 접근 제어를 사용한다.**
   - 웹 로그인만으로 끝나지 않고 Debug Mode / 내부 토큰 구조가 섞여 있다.

5. **백업/복구는 단일 파일 업로드가 아니라**
   - 설정
   - FTP 연결
   - manifest
   - staging
   - restore point
   흐름 전체로 봐야 한다.

---

## 12. 문서 활용 권장 방식

이 문서는 아래 순서로 보는 것을 권장한다.

1. `2. 전체 서비스 구성`
2. `7. API 전체 구조`
3. `9. 실제 동작 흐름`
4. `10. 로그와 확인 포인트`

그 다음에 Word 운영문서와 PPT는 이 문서를 요약/재구성해서 사용한다.
