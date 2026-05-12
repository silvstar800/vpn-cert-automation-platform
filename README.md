# SSL VPN Automation Platform Portfolio

OpenVPN 기반 SSL VPN 인증서 운영, APC 생성, IP 임대, 장비/라이선스 관리, 백업/복구, 보안 모니터링을 하나의 운영 포털로 통합한 프로젝트를 포트폴리오 관점에서 정리한 저장소입니다.

이 저장소는 실제 운영 저장소 전체를 그대로 복제한 레포가 아니라, **설계 설명 + 핵심 코드 증빙 + Docker mock 재현 환경**을 함께 보여주기 위한 포트폴리오용 스냅샷입니다.

---

## 한눈에 보기

- **프로젝트 성격**: 운영형 인프라/네트워크 자동화 플랫폼
- **핵심 기술**: `FastAPI`, `PostgreSQL`, `React`, `OpenVPN`, `nginx`, `Docker`
- **운영 대상**: 일반 SG, Legacy SG, SFOS / XGS
- **핵심 가치**: 수작업 기반 VPN 운영 절차 자동화, 운영 가시성 확보, 장애 대응 속도 향상

---

## 이 저장소에서 볼 수 있는 것

### 1. 설계 / 운영 설명
- 프로젝트 개요
- 아키텍처
- 주요 기능
- 트러블슈팅 사례
- 실제 서비스 / 포트 / API / 동작 흐름

### 2. 실제 코드 증빙
- 백엔드 핵심 API 코드
- 프론트엔드 운영 포털 코드
- manager 계층 구조
- mock 데이터 시드

### 3. Docker mock 테스트 환경
- 운영 장비 없이도 UI / API 흐름 재현 가능
- 로그인, 보안 모니터, 자산/장비, 백업 화면 검증 가능

---

## 빠르게 실행

### 실행

```bash
docker compose --env-file ./code/frontend/docker-compose.mock.env -f ./code/frontend/docker-compose.mock.yml up --build
```

### 접속

- 프론트: `http://localhost:8080`
- 백엔드 Health: `http://localhost:8443/health`

### 테스트 계정

- 웹 로그인
  - ID: `admin`
  - PW: `admin123!`
- 보안 모니터 / Debug Mode
  - PW: `debug123!`

### 종료

```bash
docker compose --env-file ./code/frontend/docker-compose.mock.env -f ./code/frontend/docker-compose.mock.yml down
```

---

## 문서 구성

- [01. 프로젝트 개요](./docs/01_project_overview.md)
- [02. 아키텍처 설명](./docs/02_architecture.md)
- [03. 주요 기능 정리](./docs/03_key_features.md)
- [04. 트러블슈팅 사례](./docs/04_troubleshooting_cases.md)
- [05. 서비스 / 포트 / API / 동작 흐름](./docs/05_service_runtime_flow.md)

아키텍처 원본:
- [draw.io 구성도](./diagrams/ssl_vpn_system_architecture.drawio)

---

## 코드 구성

- [코드 안내](./code/README.md)
- [백엔드 코드](./code/backend)
- [프론트엔드 코드](./code/frontend)

### 백엔드 바로 보기

- [FastAPI 진입점](./code/backend/app.py)
- [DB 연결](./code/backend/db.py)
- [모델 정의](./code/backend/models.py)
- [매니저 계층](./code/backend/managers)
- [Mock 모드 시드](./code/backend/mock_mode.py)

### 프론트엔드 바로 보기

- [앱 루트](./code/frontend/src/App.jsx)
- [API 클라이언트](./code/frontend/src/api/client.js)
- [대시보드 화면](./code/frontend/src/pages/DashboardPage.jsx)
- [보안 모니터 화면](./code/frontend/src/pages/SecurityMonitorPage.jsx)
- [전역 스타일](./code/frontend/src/styles/main.css)

---

## 주요 화면 미리보기

아래 파일명을 기준으로 실제 운영 화면 또는 mock 테스트 화면 캡처를 추가할 수 있습니다.

- `./images/dashboard.png`
- `./images/clients.png`
- `./images/assets.png`
- `./images/security-monitor.png`
- `./images/backup.png`

예시:

```md
![대시보드](./images/dashboard.png)
![클라이언트 목록](./images/clients.png)
![장비/라이선스](./images/assets.png)
![보안 모니터](./images/security-monitor.png)
![백업](./images/backup.png)
```

---

## 참고

- 이 저장소의 코드는 실제 운영 서버 기준 최신 구조를 반영한 포트폴리오용 코드 스냅샷입니다.
- 인프라 민감정보, 실제 운영 환경 변수, 사설 자산 정보는 제외 또는 치환되어 있습니다.
- 설계/운영 관점 설명은 Notion 포트폴리오에서, 구현 상세와 mock 테스트는 이 GitHub 저장소에서 확인하는 구조를 의도했습니다.
