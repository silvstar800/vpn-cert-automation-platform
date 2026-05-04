# 프론트엔드 코드

포트폴리오에 포함한 프론트엔드 코드는 운영자가 실제로 사용하는 SSL VPN 운영 포털 화면을 보여주기 위한 참고용 구성입니다.

## 포함 파일

- `package.json`
- `index.html`
- `src/`

## 주요 화면

- 대시보드
- 클라이언트 목록 / 상세
- 장비 / 라이선스
- 인증서 만료
- IP 임대
- 시스템 상태
- APC 요청
- 설정
- 보안 모니터

## 보면 좋은 순서

1. [src/App.jsx](./src/App.jsx)
2. [src/api/client.js](./src/api/client.js)
3. [src/pages/DashboardPage.jsx](./src/pages/DashboardPage.jsx)
4. [src/pages/SecurityMonitorPage.jsx](./src/pages/SecurityMonitorPage.jsx)
5. [src/styles/main.css](./src/styles/main.css)

## 포트폴리오 관점 포인트

- 운영 중심 UI 흐름, 페이지 분리 구조, 상태 기반 화면 전환, 보안 모니터/장비 관리 같은 업무형 화면 설계를 확인할 수 있습니다.
