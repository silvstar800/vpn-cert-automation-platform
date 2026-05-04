# SSL VPN 인증서 및 운영 자동화 플랫폼

OpenVPN 기반 SSL VPN 인증서 발급, APC 생성, IP 임대, 장비/라이선스 관리, 백업 검증, 보안 모니터링을 통합한 운영 포털 프로젝트입니다.

이 저장소는 실제 서비스 운영 저장소 전체를 그대로 옮긴 저장소가 아니라, 프로젝트를 포트폴리오 관점에서 설명하기 위해 문서, 구성도, 실제 코드 일부를 정리한 포트폴리오 저장소입니다.

---

## 프로젝트 한 줄 소개

수작업 중심이던 SSL VPN 인증서 운영 절차를 인증서 발급, 장비 관리, 백업 검증, 보안 모니터링까지 포함한 웹 기반 운영 자동화 시스템으로 전환한 프로젝트입니다.

---

## 핵심 요약

- 일반 SG / Legacy SG / SFOS 장비가 혼재된 환경에서 VPN 운영 정책을 분리해 표준화
- FastAPI + PostgreSQL + React 기반의 운영 포털 구축
- OpenVPN, CCD, 라우팅, NAT, firewalld 정책까지 운영 자동화 흐름에 포함
- 보안 모니터, 슬랙 알림, 백업/복구 검증, runtime guard로 운영 안정성 강화
- 실제 운영 서버와 동일한 기준선으로 기능과 코드를 추적할 수 있도록 정리

---

## 문서 구성

### 1. 프로젝트 개요
- [프로젝트 개요](./docs/01_project_overview.md)

### 2. 아키텍처
- [아키텍처 설명](./docs/02_architecture.md)
- [draw.io 구성도](./diagrams/ssl_vpn_system_architecture.drawio)

### 3. 주요 기능
- [주요 기능 정리](./docs/03_key_features.md)

### 4. 트러블슈팅 및 운영 경험
- [트러블슈팅 사례](./docs/04_troubleshooting_cases.md)

### 5. 실제 코드 참고
- [코드 안내](./code/README.md)
- [백엔드 코드](./code/backend)
- [프론트엔드 코드](./code/frontend)

---

## 실제 코드 바로 보기

### 백엔드 핵심 진입점
- [FastAPI 앱 진입점](./code/backend/app.py)
- [DB 연결 구성](./code/backend/db.py)
- [데이터 모델](./code/backend/models.py)
- [도메인 매니저 계층](./code/backend/managers)

### 프론트엔드 핵심 진입점
- [앱 루트](./code/frontend/src/App.jsx)
- [API 클라이언트](./code/frontend/src/api/client.js)
- [대시보드 화면](./code/frontend/src/pages/DashboardPage.jsx)
- [보안 모니터 화면](./code/frontend/src/pages/SecurityMonitorPage.jsx)
- [전역 스타일](./code/frontend/src/styles/main.css)

---

## 담당 역할

- FastAPI 기반 백엔드 API 설계 및 구현
- React 기반 운영 포털 UI 설계 및 개발
- OpenVPN 운영 구조 및 CCD/IP 임대 관리 로직 개선
- 설치 스크립트, runtime guard, 운영 자동화 스크립트 정비
- 보안 모니터링, 슬랙 알림, 백업/복구 검증 흐름 구현
- 운영 서버와 로컬/검증 환경 간 기준선 정리

---

## 기술 스택

### Backend
- Python
- FastAPI
- SQLAlchemy
- Uvicorn

### Frontend
- React
- Vite

### Database
- PostgreSQL

### Network / Infra
- OpenVPN
- firewalld
- iptables
- NAT / Routing
- systemd
- nginx
- Azure VM / VNet / NSG

### Monitoring / Alerting
- Slack Bot / Webhook
- WhatsUp Gold
- Runtime Guard

---

## 해결한 문제

- VPN 인증서 발급과 재발급이 수작업 중심이라 운영 부담이 큼
- 레거시 장비와 최신 장비가 혼재되어 포트, 상태 로그, CCD, 라우팅 정책이 복잡함
- 방화벽 / 라우팅 / NAT 변경 누락 리스크가 존재함
- 서비스 다운, 인증서 만료, 침입 시도, 백업 실패에 대한 운영 가시성이 부족함
- 재부팅 이후 서비스 / 인터페이스 / 방화벽 규칙이 틀어질 수 있어 운영 안정성이 낮음

---

## 주요 성과

- 인증서 발급, APC 생성, IP 임대, 장비/라이선스 관리 기능을 운영 포털로 통합
- 일반 SG / Legacy SG / SFOS 유형별 운영 정책을 분리하고 일관된 관리 기준 마련
- 백업 검증, 서비스 상태 확인, 슬랙 알림을 통해 운영 대응 속도 향상
- runtime guard 기반 재부팅 후 자동 점검 체계 구축
- 운영 반영본 기준으로 기능과 코드의 추적이 쉬운 구조 정리

---

## 폴더 설명

### `docs`
프로젝트 설명 문서가 들어 있습니다. 프로젝트 배경, 아키텍처, 기능, 트러블슈팅을 문서 단위로 분리했습니다.

### `diagrams`
아키텍처 구성도와 draw.io 원본 파일을 보관합니다.

### `images`
운영 화면 캡처, 발표 자료용 PNG, 다이어그램 이미지 같은 정적 자료를 보관합니다.

### `code`
포트폴리오 설명에 사용한 실제 백엔드/프론트엔드 코드 일부를 정리한 영역입니다.

---

## 포트폴리오 관점 포인트

이 프로젝트는 단순한 관리자 페이지 개발이 아니라 아래 역량을 함께 보여주는 사례로 정리했습니다.

- 내부 운영 서비스 설계
- 네트워크 및 VPN 운영 자동화
- 보안 모니터링 및 운영 통제
- 장애 대응 체계 설계
- 운영 환경 기준선 관리 및 유지보수성 개선

---

## 추천 읽는 순서

1. [프로젝트 개요](./docs/01_project_overview.md)
2. [아키텍처 설명](./docs/02_architecture.md)
3. [주요 기능 정리](./docs/03_key_features.md)
4. [트러블슈팅 사례](./docs/04_troubleshooting_cases.md)
5. [코드 안내](./code/README.md)
