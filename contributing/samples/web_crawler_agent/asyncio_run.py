"""웹 크롤링 에이전트 실행 스크립트"""
import asyncio
import sys
import os

# 현재 디렉토리의 상위 경로를 추가하여 importing 문제 해결
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from contributing.samples.web_crawler_agent.agent import create_agent


async def run_web_crawler_agent():
    """웹 크롤링 에이전트를 실행합니다"""
    # 에이전트 생성
    agent = create_agent()
    
    # 인사말
    print("=" * 50)
    print("웹 크롤링 에이전트에 오신 것을 환영합니다!")
    print("이 에이전트는 웹 검색과 크롤링을 통해 질문에 답변합니다.")
    print("종료하려면 '종료'를 입력하세요.")
    print("=" * 50)
    
    # 대화 루프
    while True:
        # 사용자 입력 받기
        user_input = input("\n질문: ")
        
        # 종료 조건 확인
        if user_input.lower() in ["종료", "quit", "exit"]:
            print("에이전트를 종료합니다. 감사합니다!")
            break
        
        # 에이전트에 질문 전달
        print("\n에이전트가 응답 중...\n")
        try:
            # InMemoryRunner 생성
            from google.adk.runners import InMemoryRunner
            
            # 에이전트 실행
            runner = InMemoryRunner(agent)
            session_id = "web_crawler_session"
            
            # 세션 생성 또는 불러오기
            from google.genai import types
            
            session = runner.session_service.get_or_create_session(
                app_name=runner.app_name,
                user_id="web_crawler_user",
                session_id=session_id
            )
            
            # 사용자 메시지 생성
            user_message = types.Content(
                parts=[types.Part(text=user_input)]
            )
            
            # 에이전트 실행 및 응답 수집
            response_text = ""
            async for event in runner.run(
                session=session,
                new_message=user_message
            ):
                if event.content and hasattr(event.content, 'parts'):
                    for part in event.content.parts:
                        if hasattr(part, 'text') and part.text:
                            response_text += part.text
            
            print(f"응답: {response_text}")
        except Exception as e:
            print(f"오류 발생: {str(e)}")


if __name__ == "__main__":
    # 비동기 함수 실행
    asyncio.run(run_web_crawler_agent())
