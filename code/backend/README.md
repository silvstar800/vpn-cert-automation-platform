# 백엔드 코드

포트폴리오에 포함한 백엔드 코드는 운영 포털의 핵심 API와 도메인 로직을 보여주기 위한 참고용 구성입니다.

## 포함 파일

- `app.py`
- `db.py`
- `models.py`
- `managers/`
- `mock_mode.py`
- `requirements.txt`
- `Dockerfile.backend`

## 주요 역할

- 인증서 발급 / 재발급 API
- APC 요청 처리
- IP 임대 관리
- 장비 / 라이선스 관리 API
- 백업 / 복구 검증 흐름
- 보안 모니터 및 알림 처리
- mock 데이터 시드 및 테스트 환경 분기

## 보면 좋은 순서

1. [app.py](./app.py)
2. [models.py](./models.py)
3. [db.py](./db.py)
4. [managers](./managers)
5. [mock_mode.py](./mock_mode.py)

## 포트폴리오 관점 포인트

- 단순 CRUD를 넘어서 인증서 운영, VPN 정책, 백업, 보안 모니터링까지 함께 다루는 운영형 API 구조를 확인할 수 있습니다.
- 운영 코드와 별개로 mock Docker 테스트가 가능하도록 구성해 UI/백엔드 흐름을 재현할 수 있습니다.
