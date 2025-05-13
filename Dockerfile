FROM python:3.10-slim

WORKDIR /app

# 기본 종속성 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    graphviz \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# UV 도구 설치
RUN pip install --no-cache-dir uv

# 작업 디렉토리 복사
COPY . /app/

# 가상 환경 생성 및 패키지 설치
RUN python -m venv .venv && \
    . .venv/bin/activate && \
    pip install --upgrade pip && \
    pip install uv && \
    uv sync --all-extras

# 개발 환경을 위한 환경 변수 설정
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app:$PYTHONPATH"

# 포트 노출 (FastAPI 애플리케이션 용)
EXPOSE 8000

# 개발 모드에서 실행하기 위한 기본 명령어
CMD ["bash"]
