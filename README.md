# 🔐 SSL VPN Automation & Network Operations Platform

## 📌 Overview
멀티 장비 환경(Sophos UTM9 → SFOS 전환)에서 VPN 인증서 발급, 네트워크 정책 관리, 모니터링을 자동화하기 위해 구축한 보안 운영 플랫폼입니다.

기존 수작업 기반 운영을 개선하여, 인증서 발급부터 네트워크 정책 적용, 장애 감지까지 하나의 시스템에서 처리할 수 있도록 설계했습니다.

---

## 🎯 Problem

- VPN 인증서 발급 및 재발급 수작업 처리
- 레거시(UTM9)와 신규(SFOS) 장비 혼재
- 방화벽/라우팅 정책 변경 누락 위험
- 장애 발생 시 상태 추적 어려움
- 운영 환경과 개발 환경 불일치

---

## 💡 Solution

- VPN 인증서 발급 및 재발급 자동화
- 장비 유형별 네트워크 정책 자동 처리
- IP 임대 및 CCD 기반 클라이언트 관리
- runtime guard 기반 상태 점검 및 자동 복구
- Slack 기반 실시간 알림 시스템 구축
- 운영 서버 기준 소스 동기화

---

## 🏗️ Architecture
추가 예정

---

## ⚙️ Tech Stack

### Backend
- Python (FastAPI)
- Uvicorn

### Frontend
- React

### Database
- PostgreSQL

### Network / Infra
- OpenVPN (CCD, TLS, PKI)
- iptables / firewalld / NAT / Routing

### Monitoring
- WhatsUp Gold
- Slack Webhook / Bot

### Automation
- Bash Script
- Runtime Guard

### Cloud
- Azure (VNet, NSG, VPN Gateway)
- AWS (VPC, EC2, CloudFormation)

---

## 🔑 Key Features

### 🔐 VPN Certificate Automation
- 인증서 발급/재발급 자동화
- OpenVPN 기반 PKI 관리

### 🌐 IP Allocation & CCD
- 클라이언트별 IP 자동 할당
- CCD 기반 정책 분리

### ⚡ Network Policy Automation
- 장비 유형별 포트/라우팅/NAT 자동 처리

### 🛠 Runtime Guard
- 서비스 상태 점검
- 방화벽/라우팅 자동 복구

### 📊 Monitoring & Alert
- Slack 기반 실시간 알림
- 인증서 만료 / 장애 감지

---

## 🚨 Troubleshooting Case

### NAC 인증 장애 분석

#### Issue
- NAC 인증이 정상적으로 동작하지 않는 문제 발생

#### Analysis
- Wireshark 기반 패킷 캡처
- 방화벽 로그와 비교 분석

#### Root Cause
- 특정 구간에서 인증 트래픽 누락

#### Solution
- 방화벽 정책 및 라우팅 수정

---

## 📈 Result

- VPN 운영 절차 자동화
- 장애 대응 속도 향상
- 멀티 환경 통합 관리 가능
- 운영 일관성 확보

---

## 🔥 What I Learned

- 네트워크 흐름 기반 문제 해결 능력
- 멀티 환경(VPN/Firewall/Cloud) 통합 설계 경험
- 운영 자동화의 중요성
- 인프라를 코드화하는 접근 방식

---

## 📌 Future Improvements

- BGP 기반 확장 구조 적용
- Kubernetes 기반 확장성 개선
- Observability 강화 (Prometheus / Grafana)
