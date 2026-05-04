# SSL VPN 인증서 및 운영 자동화 플랫폼

OpenVPN 기반 SSL VPN 인증서 발급, APC 생성, IP 임대, 장비/라이선스 관리, 백업 검증, 보안 모니터링을 통합한 운영 포털 프로젝트입니다.

이 저장소는 실제 서비스 소스 저장소가 아니라, 프로젝트를 포트폴리오 관점에서 정리한 문서형 저장소입니다.  
실제 운영 환경에서 해결했던 문제, 아키텍처 구성, 운영 자동화 전략, 트러블슈팅 사례를 중심으로 정리했습니다.

---

## 프로젝트 한 줄 소개

수작업 중심이던 SSL VPN 인증서 운영 절차를 인증서 발급, 장비 관리, 백업 검증, 보안 모니터링까지 포함한 웹 기반 운영 자동화 시스템으로 전환한 프로젝트입니다.

---

## 핵심 요약

- 일반 SG / Legacy SG / SFOS 장비가 혼재된 환경에서 VPN 운영 정책을 표준화
- FastAPI + PostgreSQL + React 기반 운영 포털 구축
- OpenVPN, CCD, 라우팅, NAT, firewalld 정책을 운영 흐름과 연계해 자동화
- 보안 모니터, 슬랙 알림, 백업/복구 검증, runtime guard를 통해 운영 안정성 강화
- 운영 서버와 로컬/검증 환경의 기준선을 맞춰 유지보수성을 높임

---

## 문서 구성

### 1. 프로젝트 개요
- [프로젝트 개요](./docs/01_project_overview.md)

### 2. 아키텍처
- [아키텍처 설명](./docs/02_architecture.md)
- [draw.io 인프라 구성도](./diagrams/ssl_vpn_system_architecture.drawio)

### 3. 주요 기능
- [주요 기능 정리](./docs/03_key_features.md)

### 4. 트러블슈팅 및 운영 경험
- [트러블슈팅 사례](./docs/04_troubleshooting_cases.md)

### 5. 실제 코드 참고
- [백엔드 코드](./code/backend)
- [프론트엔드 코드](./code/frontend)

---

## 담당 역할

- FastAPI 기반 백엔드 API 설계 및 구현
- React 기반 운영 포털 UI 설계 및 개발
- OpenVPN 운영 구조 및 CCD/IP 임대 관리 로직 개선
- 설치 스크립트, runtime guard, 운영 자동화 스크립트 정비
- 보안 모니터링, 슬랙 알림, 백업/복구 검증 흐름 구현
- 운영 서버와 로컬/검증본 간 정합성 관리

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

## 프로젝트에서 해결한 문제

- VPN 인증서 발급/재발급이 수작업 중심으로 운영됨
- 레거시 장비와 최신 장비가 혼재되어 포트, 상태 로그, CCD, 라우팅 정책이 복잡함
- 방화벽/라우팅/NAT 변경 누락 시 장애가 발생하기 쉬움
- 서비스 다운, 인증서 만료, 침입 시도, 백업 실패에 대한 운영 가시성이 부족함
- 재부팅 이후 서비스/인터페이스/방화벽 규칙이 틀어질 수 있어 운영 안정성이 낮음

---

## 주요 성과

- 인증서 발급, APC 생성, IP 임대, 장비/라이선스 관리 기능을 운영 포털로 통합
- 일반 SG / Legacy SG / SFOS 유형별 운영 정책을 분리하고 구조화
- 백업 검증, 서비스 상태 확인, 슬랙 알림을 통해 운영 대응 속도 향상
- runtime guard 기반 재부팅 후 자동 점검 체계 구축
- 운영 반영본과 로컬/검증본을 동기화해 유지보수 기준선 정리

---

## 폴더 설명

### `docs`
프로젝트 설명 문서가 들어 있습니다.  
프로젝트 개요, 아키텍처, 주요 기능, 트러블슈팅을 문서 단위로 분리했습니다.

### `diagrams`
draw.io 등 아키텍처 다이어그램 원본 파일을 보관하는 폴더입니다.

### `images`
실제 운영 화면 캡처, 발표 자료 이미지, 다이어그램 PNG 같은 정적 리소스를 보관하는 폴더입니다.

### `code`
포트폴리오 설명에 사용한 실제 프로젝트의 핵심 백엔드/프론트엔드 소스를 참고용으로 포함한 폴더입니다.

---

## 포트폴리오 관점 포인트

이 프로젝트는 단순한 관리자 페이지 개발이 아니라 아래 영역을 함께 다룬 사례로 설명할 수 있습니다.

- 웹 서비스 개발
- 네트워크 운영 자동화
- 보안 운영
- 장애 대응 체계 설계
- 운영 환경 표준화 및 유지보수성 개선

---

## 추천 읽는 순서

1. [프로젝트 개요](./docs/01_project_overview.md)
2. [아키텍처 설명](./docs/02_architecture.md)
3. [주요 기능 정리](./docs/03_key_features.md)
4. [트러블슈팅 사례](./docs/04_troubleshooting_cases.md)
5. [실제 코드 참고](./code/backend)

