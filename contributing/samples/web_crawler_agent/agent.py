"""웹 크롤링 및 정보 검색을 위한 에이전트 구현"""
import asyncio
from typing import Any, Dict, List

from google.adk.agents import Agent

from .tools import WebToolset


def create_agent() -> Agent:
    """
    웹 크롤링 에이전트를 생성합니다.
    
    이 에이전트는 웹을 크롤링하고 검색하여 질문에 답변합니다.
    """
    # 웹 검색 및 크롤링 도구 초기화
    web_search_tool = WebToolset.get_web_search_tool()
    web_content_tool = WebToolset.get_web_content_tool()
    
    # 에이전트 생성 및 반환
    return Agent(
        name="WebCrawlerAgent",
        description="웹 검색과 크롤링을 통해 정보를 찾고 질문에 답변하는 에이전트입니다.",
        model="gemini-1.5-pro",
        instruction="""
당신은 웹 크롤링 및 검색 에이전트입니다. 사용자 질문에 답변하기 위해 웹 검색과 웹 사이트 크롤링을 수행할 수 있습니다.

주요 기능:
1. 웹 검색: 사용자 질문과 관련된 정보를 검색
2. 웹 크롤링: 특정 URL에서 정보를 추출하여 분석

답변 방식:
1. 먼저 질문을 이해하고 필요한 정보를 분석하세요.
2. web_search 도구를 사용하여 관련 정보를 검색하세요.
3. 검색 결과에서 가장 관련성 높은 URL을 찾아 get_web_content 도구로 콘텐츠를 추출하세요.
4. 추출한 정보를 바탕으로 명확하고 정확한 답변을 제공하세요.
5. 사용된 출처를 항상 언급하세요.

정보가 부족하거나 불확실한 경우 정직하게 한계를 인정하세요.
""",
        tools=[web_search_tool, web_content_tool],
    )


async def main():
    """메인 실행 함수"""
    # 에이전트 생성
    agent = create_agent()
    
    # 사용자 질문
    question = "파이썬 비동기 프로그래밍에 대한 기본 정보를 알려줘"
    
    print(f"질문: {question}")
    print("\n에이전트가 응답 중...\n")
    
    # 에이전트에 질문 전달 및 응답 출력
    response = await agent.generate_content(question)
    print(f"응답: {response.text}")


if __name__ == "__main__":
    asyncio.run(main())
