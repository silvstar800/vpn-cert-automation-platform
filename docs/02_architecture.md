# 아키텍처 설명

## 구성 개요

이 시스템은 고객사 장비가 OpenVPN 터널을 통해 운영 서버와 연결되고, 운영 서버 내부의 FastAPI / PostgreSQL / Runtime Guard / 백업 및 알림 체계가 이를 지원하는 구조입니다.

## 주요 구성 요소

### 1. Customer On-Premise
- 고객사 UTM / SFOS / XGS 장비
- 내부 사용자, 서버, 네트워크와 연결
- SSL VPN 터널의 종단점 역할 수행

### 2. Azure 운영 서버
- SSL VPN 서버(OpenVPN)
- FastAPI 백엔드
- PostgreSQL 운영 DB
- Runtime Guard 및 운영 스크립트
- nginx 기반 HTTPS 진입점

### 3. 운영 포털
- React/Vite 기반 UI
- 인증서 발급 / 재발급
- 클라이언트 및 IP 임대 관리
- 장비 / 라이선스 관리
- APC 요청
- 설정 / 보안 모니터 / 백업 관리

### 4. 운영 자동화 계층
- CCD 및 IP 임대 관리
- 라우팅 / NAT / firewalld direct rule 적용
- 재부팅 후 서비스 및 인터페이스 자동 점검
- 백업 검증 및 슬랙 알림

### 5. 외부 연동
- Slack 알림 채널
- FTP / NAS 백업 저장소
- WhatsUp Gold 모니터링

## 구조상 핵심 포인트

- 일반 SG, Legacy SG, SFOS를 서로 다른 대역/로그/정책으로 분리
- 운영 포털에서 인증서/장비/모니터링/백업 업무를 통합 관리
- 재부팅 이후에도 서비스와 방화벽 상태를 자동 점검하는 구조
- 운영 환경과 로컬 검증 환경 간 정합성을 유지할 수 있는 기준선 확보

## 다이어그램

- [draw.io 인프라 구성도](../diagrams/ssl_vpn_system_architecture.drawio)

