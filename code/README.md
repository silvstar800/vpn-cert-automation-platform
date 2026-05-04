# 실제 코드 안내

이 폴더는 포트폴리오 설명에 사용한 실제 프로젝트 코드 일부를 참고용으로 정리한 영역입니다.

## 구성

### `backend`
- FastAPI 기반 운영 API 코드
- 인증서 발급, APC 요청, IP 임대, 장비/라이선스, 백업/복구, 보안 모니터링 관련 로직 포함
- mock 데이터 시드 및 Docker 테스트용 설정 포함

### `frontend`
- React / Vite 기반 운영 포털 UI 코드
- 대시보드, 클라이언트 관리, 장비/라이선스, APC 요청, 보안 모니터 등 실제 운영 화면 포함
- mock nginx 프록시 및 Docker 테스트 설정 포함

## 빠르게 보기

### 백엔드
- [앱 진입점](./backend/app.py)
- [DB 연결](./backend/db.py)
- [모델 정의](./backend/models.py)
- [매니저 계층](./backend/managers)
- [Mock 모드 시드](./backend/mock_mode.py)

### 프론트엔드
- [앱 루트](./frontend/src/App.jsx)
- [API 클라이언트](./frontend/src/api/client.js)
- [대시보드 화면](./frontend/src/pages/DashboardPage.jsx)
- [보안 모니터 화면](./frontend/src/pages/SecurityMonitorPage.jsx)
- [전역 스타일](./frontend/src/styles/main.css)
- [Mock compose](./frontend/docker-compose.mock.yml)
- [Mock nginx 설정](./frontend/nginx.mock.conf)

## 참고

- 실제 운영 저장소 전체를 그대로 옮긴 것은 아니며, 포트폴리오 설명에 필요한 핵심 파일 위주로 정리했습니다.
- 인프라 설정, 민감정보, 배포 환경값은 제외하거나 분리된 상태로 관리합니다.
- 테스트용 Docker mock 실행 기준 계정은 `admin / admin123!`, 보안 모니터 비밀번호는 `debug123!`입니다.
