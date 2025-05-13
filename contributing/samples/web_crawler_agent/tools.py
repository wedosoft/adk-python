"""웹 크롤링 도구 클래스 정의"""
from typing import List, Dict, Any, Optional
import asyncio

import requests
from bs4 import BeautifulSoup
from newspaper import Article
from langchain_community.tools import DuckDuckGoSearchResults
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.function_tool import FunctionTool
from llama_index.readers.web import SimpleWebPageReader
from pydantic import BaseModel, Field


class WebSearch(BaseModel):
    """웹 검색 결과"""
    query: str = Field(description="수행할 검색 쿼리")
    num_results: int = Field(default=3, description="반환할 검색 결과 수")


class WebContent(BaseModel):
    """웹 콘텐츠 가져오기 결과"""
    url: str = Field(description="콘텐츠를 가져올 웹페이지 URL")


class WebToolset:
    """웹 검색 및 크롤링을 위한 도구 모음"""

    @staticmethod
    def get_web_search_tool() -> BaseTool:
        """웹 검색 도구"""
        async def web_search(query: str, num_results: int = 3) -> List[Dict]:
            """DuckDuckGo를 사용하여 웹 검색을 수행합니다"""
            search_tool = DuckDuckGoSearchResults(num_results=num_results)
            results = search_tool.invoke(query)
            return results
        
        return FunctionTool(web_search)
    
    @staticmethod
    def get_web_content_tool() -> BaseTool:
        """웹 콘텐츠 추출 도구"""
        async def get_web_content(url: str) -> str:
            """URL에서 웹 콘텐츠를 추출합니다"""
            try:
                # LlamaIndex를 사용한 웹 페이지 읽기
                documents = SimpleWebPageReader(html_to_text=True).load_data([url])
                if documents and len(documents) > 0:
                    return documents[0].text
                
                # 백업 방법: newspaper 사용
                article = Article(url)
                article.download()
                article.parse()
                if article.text:
                    return article.text
                
                # 마지막 방법: BeautifulSoup 사용
                response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
                soup = BeautifulSoup(response.text, "html.parser")
                # 메타 데이터 및 메인 콘텐츠 추출
                title = soup.title.string if soup.title else ""
                text = ""
                for p in soup.find_all(["p", "h1", "h2", "h3", "article"]):
                    text += p.get_text() + "\n"
                
                return f"제목: {title}\n\n내용:\n{text}"
            except Exception as e:
                return f"웹 콘텐츠를 추출하는 동안 오류가 발생했습니다: {str(e)}"
        
        return FunctionTool(get_web_content)