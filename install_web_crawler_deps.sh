#!/bin/bash

# 웹 크롤링 에이전트를 위한 패키지 설치 스크립트

echo "웹 크롤링 에이전트 구축을 위한 패키지 설치 중..."

# 가상 환경 활성화 (이미 활성화되어 있을 수 있음)
if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
fi

# 필요한 패키지 설치
pip install beautifulsoup4 requests llama-index-readers-web langchain-community duckduckgo-search newspaper3k

echo "패키지 설치 완료!"
