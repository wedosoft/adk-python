# ADK-Python Docker 개발 환경

이 문서는 ADK-Python 프로젝트의 Docker 개발 환경 설정 및 사용 방법을 설명합니다.

## 필수 요구 사항

- Docker
- Docker Compose

## 환경 정보

- Python 3.10
- ADK-Python 의존성 패키지 전체 설치

## 빠른 시작

### 1. Docker 이미지 빌드

```bash
./docker-dev.sh build
```

### 2. 개발 컨테이너 시작

```bash
./docker-dev.sh up
```

### 3. 컨테이너 쉘에 접속

```bash
./docker-dev.sh shell
```

### 4. 개발 컨테이너 중지

```bash
./docker-dev.sh down
```

## 개발 작업

개발 컨테이너 내부에서 다음 명령어를 사용하여 일반적인 개발 작업을 수행할 수 있습니다:

### 유닛 테스트 실행

```bash
./docker-dev.sh test
```

또는 컨테이너 내부에서:

```bash
uv run pytest ./tests/unittests
```

### 코드 포맷팅

```bash
./docker-dev.sh format
```

또는 컨테이너 내부에서:

```bash
uv run pyink --config pyproject.toml ./src
```

### 패키지 빌드

```bash
./docker-dev.sh build-pkg
```

또는 컨테이너 내부에서:

```bash
uv build
```

## 주의 사항

- 프로젝트 디렉토리가 컨테이너 내부의 `/app` 디렉토리에 마운트됩니다.
- 컨테이너 내부에서 수행한 모든 파일 변경 사항은 호스트 시스템에도 반영됩니다.
- FastAPI 서버는 8000 포트를 사용합니다.

## Docker 구성 사용자 정의

필요에 따라 `Dockerfile` 및 `docker-compose.yml` 파일을 수정하여 개발 환경을 사용자 정의할 수 있습니다.
