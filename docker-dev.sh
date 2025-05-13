#!/bin/bash

# ADK-Python 개발을 위한 도커 환경 도우미 스크립트

# 색상 코드
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 사용법 표시
function show_usage {
  echo -e "${BLUE}ADK-Python 개발 도커 환경 도우미${NC}"
  echo
  echo "사용법:"
  echo "  ./docker-dev.sh [명령어]"
  echo
  echo "명령어:"
  echo "  build      - Docker 이미지 빌드"
  echo "  up         - 개발 컨테이너 시작 및 백그라운드 실행"
  echo "  down       - 개발 컨테이너 중지"
  echo "  shell      - 실행 중인 컨테이너에 쉘로 접속"
  echo "  test       - 유닛 테스트 실행"
  echo "  format     - pyink로 코드 포맷팅"
  echo "  build-pkg  - 패키지 빌드"
  echo "  help       - 이 도움말 표시"
  echo
  exit 1
}

# 명령어가 없으면 사용법 표시
if [ $# -eq 0 ]; then
  show_usage
fi

# 명령어 처리
case "$1" in
  build)
    echo -e "${GREEN}Docker 이미지 빌드 중...${NC}"
    docker-compose build
    ;;
  
  up)
    echo -e "${GREEN}개발 컨테이너 시작 중...${NC}"
    docker-compose up -d
    echo -e "${YELLOW}컨테이너에 접속하려면 './docker-dev.sh shell' 명령어를 사용하세요.${NC}"
    ;;
  
  down)
    echo -e "${GREEN}개발 컨테이너 중지 중...${NC}"
    docker-compose down
    ;;
  
  shell)
    echo -e "${GREEN}개발 컨테이너 쉘로 접속 중...${NC}"
    docker-compose exec adk-python-dev bash
    ;;
  
  test)
    echo -e "${GREEN}유닛 테스트 실행 중...${NC}"
    docker-compose exec adk-python-dev bash -c "cd /app && . .venv/bin/activate && uv run pytest ./tests/unittests"
    ;;

  format)
    echo -e "${GREEN}코드 포맷팅 중...${NC}"
    docker-compose exec adk-python-dev bash -c "cd /app && . .venv/bin/activate && uv run pyink --config pyproject.toml ./src"
    ;;

  build-pkg)
    echo -e "${GREEN}패키지 빌드 중...${NC}"
    docker-compose exec adk-python-dev bash -c "cd /app && . .venv/bin/activate && uv build"
    ;;

  help)
    show_usage
    ;;
  
  *)
    echo -e "${YELLOW}알 수 없는 명령어: $1${NC}"
    show_usage
    ;;
esac
