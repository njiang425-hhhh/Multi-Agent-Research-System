"""支持异步和熔断器的网络搜索与内容提取工具。"""

import asyncio
import re
import time
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlparse
import logging
from enum import Enum
from abc import ABC, abstractmethod

import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS
from tavily import AsyncTavilyClient

from src.state import SearchResult
from src.exceptions import (
    SearchError,
    RateLimitError,
    ContentExtractionError,
    CircuitOpenError
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# 熔断器实现
# =============================================================================

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """外部服务调用的熔断器。"""
    
    name: str
    failure_threshold: int = 5
    reset_timeout: float = 30.0
    half_open_max_calls: int = 1
    
    _failures: int = field(default=0, init=False)
    _successes: int = field(default=0, init=False)
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _last_failure_time: Optional[float] = field(default=None, init=False)
    _half_open_calls: int = field(default=0, init=False)
    
    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if self._last_failure_time and (time.time() - self._last_failure_time) >= self.reset_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                logger.info(f"熔断器“{self.name}”切换为 HALF_OPEN 状态")
        return self._state
    
    def can_execute(self) -> bool:
        state = self.state
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.HALF_OPEN:
            return self._half_open_calls < self.half_open_max_calls
        return False
    
    def record_success(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            self._successes += 1
            if self._successes >= self.half_open_max_calls:
                self._state = CircuitState.CLOSED
                self._failures = 0
                self._successes = 0
                logger.info(f"熔断器“{self.name}”恢复成功后已关闭")
        else:
            self._failures = 0
    
    def record_failure(self) -> None:
        self._failures += 1
        self._last_failure_time = time.time()
        
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            logger.warning(f"熔断器“{self.name}”半开状态失败后重新打开")
        elif self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(f"熔断器“{self.name}”在失败 {self._failures} 次后打开")
    
    def get_retry_after(self) -> float:
        if self._last_failure_time:
            elapsed = time.time() - self._last_failure_time
            return max(0, self.reset_timeout - elapsed)
        return self.reset_timeout


# =============================================================================
# HTTP 客户端管理器（连接池）
# =============================================================================

class HTTPClientManager:
    """管理共享的 httpx AsyncClient 和连接池。"""
    
    _instance: Optional['HTTPClientManager'] = None
    
    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._lock = asyncio.Lock()
    
    @classmethod
    def get_instance(cls) -> 'HTTPClientManager':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    async def get_client(self) -> httpx.AsyncClient:
        async with self._lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.AsyncClient(
                    limits=httpx.Limits(
                        max_connections=50,
                        max_keepalive_connections=20,
                        keepalive_expiry=30.0
                    ),
                    timeout=httpx.Timeout(15.0, connect=5.0),
                    follow_redirects=True,
                    http2=True,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.9',
                    }
                )
            return self._client
    
    async def close(self) -> None:
        async with self._lock:
            if self._client and not self._client.is_closed:
                await self._client.aclose()
                self._client = None


# =============================================================================
# URL 验证
# =============================================================================

BLOCKED_HOSTS = {'localhost', '127.0.0.1', '0.0.0.0', '::1', '[::1]'}
BLOCKED_SCHEMES = {'file', 'ftp', 'data', 'javascript'}


def is_valid_url(url: str) -> bool:
    """检查 URL 是否有效且可以安全访问。"""
    try:
        result = urlparse(url)
        if not all([result.scheme, result.netloc]):
            return False
        if result.scheme.lower() not in {'http', 'https'}:
            return False
        hostname = result.hostname or ''
        if hostname.lower() in BLOCKED_HOSTS:
            return False
        return True
    except Exception:
        return False


# =============================================================================
# 搜索提供商抽象
# =============================================================================

class SearchProvider(ABC):
    """搜索提供商的抽象基类。"""
    
    @abstractmethod
    async def search(self, query: str, max_results: int) -> List[SearchResult]:
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        pass


class DuckDuckGoProvider(SearchProvider):
    """带速率限制和熔断器的 DuckDuckGo 搜索提供商。"""
    
    def __init__(self, max_results: int = 5):
        self.max_results = max_results
        self.last_search_time = 0.0
        self.min_delay = 2.0
        self.circuit_breaker = CircuitBreaker(
            name="duckduckgo",
            failure_threshold=3,
            reset_timeout=60.0
        )
    
    @property
    def name(self) -> str:
        return "duckduckgo"
    
    async def search(self, query: str, max_results: Optional[int] = None) -> List[SearchResult]:
        if not self.circuit_breaker.can_execute():
            retry_after = self.circuit_breaker.get_retry_after()
            raise CircuitOpenError("duckduckgo", retry_after)
        
        results_count = max_results or self.max_results
        
        try:
            elapsed = time.time() - self.last_search_time
            if elapsed < self.min_delay:
                wait_time = self.min_delay - elapsed
                logger.debug(f"触发速率限制，等待 {wait_time:.1f} 秒")
                await asyncio.sleep(wait_time)
            
            logger.info(f"正在使用 DuckDuckGo 搜索：{query}")
            
            results = await self._execute_search(query, results_count)
            
            self.last_search_time = time.time()
            self.circuit_breaker.record_success()
            
            logger.info(f"找到 {len(results)} 个结果：{query}")
            return results
            
        except CircuitOpenError:
            raise
        except Exception as e:
            self.circuit_breaker.record_failure()
            self.last_search_time = time.time()
            
            error_str = str(e).lower()
            if "ratelimit" in error_str or "202" in error_str:
                raise RateLimitError(
                    message=f"DuckDuckGo 速率限制：{str(e)}",
                    retry_after=60,
                    service="duckduckgo"
                )
            
            raise SearchError(f"搜索失败：'{query}'", details=str(e))
    
    async def _execute_search(self, query: str, max_results: int) -> List[SearchResult]:
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                search_results = await asyncio.to_thread(
                    self._sync_search, query, max_results
                )
                return search_results
            except Exception as e:
                error_str = str(e).lower()
                if ("ratelimit" in error_str or "202" in error_str) and attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 5
                    logger.warning(f"触发速率限制，等待 {wait_time} 秒（第 {attempt + 1}/{max_retries} 次尝试）")
                    await asyncio.sleep(wait_time)
                else:
                    raise
        
        return []
    
    def _sync_search(self, query: str, max_results: int) -> List[SearchResult]:
        results = []
        ddgs = DDGS()
        search_results = list(ddgs.text(query, max_results=max_results))
        
        for result in search_results:
            results.append(SearchResult(
                query=query,
                title=result.get("title", ""),
                url=result.get("href", ""),
                snippet=result.get("body", "")
            ))
        
        return results


class TavilyProvider(SearchProvider):
    """带熔断器的 Tavily 搜索提供商。"""

    def __init__(self, api_key: Optional[str] = None, max_results: int = 5):
        self.max_results = max_results
        self.client = AsyncTavilyClient(api_key=api_key)
        self.circuit_breaker = CircuitBreaker(
            name="tavily",
            failure_threshold=3,
            reset_timeout=60.0
        )

    @property
    def name(self) -> str:
        return "tavily"

    async def search(self, query: str, max_results: Optional[int] = None) -> List[SearchResult]:
        if not self.circuit_breaker.can_execute():
            retry_after = self.circuit_breaker.get_retry_after()
            raise CircuitOpenError("tavily", retry_after)

        results_count = max_results or self.max_results

        try:
            logger.info(f"正在使用 Tavily 搜索：{query}")

            response = await self.client.search(
                query=query,
                max_results=results_count,
                search_depth="basic",
            )

            self.circuit_breaker.record_success()

            results = []
            for item in response.get("results", []):
                results.append(SearchResult(
                    query=query,
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", ""),
                ))

            logger.info(f"找到 {len(results)} 个结果：{query}")
            return results

        except CircuitOpenError:
            raise
        except Exception as e:
            self.circuit_breaker.record_failure()

            error_str = str(e).lower()
            if "rate" in error_str or "limit" in error_str or "429" in error_str:
                raise RateLimitError(
                    message=f"Tavily 速率限制：{str(e)}",
                    retry_after=60,
                    service="tavily"
                )

            raise SearchError(f"搜索失败：'{query}'", details=str(e))


class WebSearchTool:
    """支持提供商抽象和回退机制的网络搜索工具。"""
    
    def __init__(self, max_results: int = 5, providers: Optional[List[SearchProvider]] = None):
        self.max_results = max_results
        self.providers = providers or [DuckDuckGoProvider(max_results)]
    
    def search(self, query: str) -> List[SearchResult]:
        """同步搜索——在事件循环中运行异步搜索。"""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(self.search_async(query))
    
    async def search_async(self, query: str) -> List[SearchResult]:
        """带提供商回退机制的异步搜索。"""
        last_error: Optional[Exception] = None
        
        for provider in self.providers:
            try:
                return await provider.search(query, self.max_results)
            except CircuitOpenError as e:
                logger.warning(f"提供商 {provider.name} 的熔断器已打开，尝试下一个提供商")
                last_error = e
                continue
            except RateLimitError as e:
                logger.warning(f"提供商 {provider.name} 触发速率限制：{e}")
                last_error = e
                continue
            except SearchError as e:
                logger.error(f"提供商 {provider.name} 出错：{e}")
                last_error = e
                continue
        
        if last_error:
            logger.error(f"所有搜索提供商均失败。最后一个错误：{last_error}")
        return []


# =============================================================================
# 内容提取器（使用 httpx 的真正异步实现）
# =============================================================================

class ContentExtractor:
    """使用 httpx 提取并清理网页内容。"""
    
    CONTENT_SELECTORS = [
        'article',
        'main',
        '[role="main"]',
        '.post-content',
        '.article-content',
        '.entry-content',
        '.content',
        '#content',
        '.post',
        '.article'
    ]
    
    REMOVE_SELECTORS = [
        'script', 'style', 'nav', 'footer', 'header', 'aside',
        '.sidebar', '.navigation', '.menu', '.comments', '.comment',
        '.advertisement', '.ad', '.ads', '.social-share', '.related-posts',
        '[role="navigation"]', '[role="complementary"]'
    ]
    
    def __init__(self, timeout: int = 15, max_content_length: int = 8000):
        self.timeout = timeout
        self.max_content_length = max_content_length
        self.client_manager = HTTPClientManager.get_instance()
        self.circuit_breaker = CircuitBreaker(
            name="content_extraction",
            failure_threshold=10,
            reset_timeout=30.0
        )
    
    def extract_content(self, url: str) -> Optional[str]:
        """同步内容提取——在事件循环中运行异步提取。"""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(self.extract_content_async(url))
    
    async def extract_content_async(self, url: str) -> Optional[str]:
        """使用 httpx 异步提取内容。"""
        if not is_valid_url(url):
            logger.warning(f"无效 URL：{url}")
            return None
        
        if not self.circuit_breaker.can_execute():
            logger.warning("内容提取熔断器已打开")
            return None
        
        try:
            logger.info(f"正在提取内容：{url}")
            
            client = await self.client_manager.get_client()
            response = await client.get(url, timeout=self.timeout)
            response.raise_for_status()
            
            content_type = response.headers.get('content-type', '').lower()
            if not any(ct in content_type for ct in ['text/html', 'application/xhtml']):
                logger.warning(f"不支持的内容类型：{content_type}")
                return None
            
            html_content = response.text
            extracted = self._parse_html(html_content)
            
            self.circuit_breaker.record_success()
            
            if extracted:
                logger.info(f"已从 {url} 提取 {len(extracted)} 个字符")
            
            return extracted
            
        except httpx.HTTPStatusError as e:
            self.circuit_breaker.record_failure()
            raise ContentExtractionError(
                f"HTTP 错误：{e.response.status_code}",
                url=url,
                status_code=e.response.status_code
            )
        except httpx.TimeoutException:
            self.circuit_breaker.record_failure()
            logger.warning(f"从 {url} 提取内容超时")
            return None
        except Exception as e:
            self.circuit_breaker.record_failure()
            logger.warning(f"从 {url} 提取内容失败：{str(e)}")
            return None
    
    def _parse_html(self, html: str) -> Optional[str]:
        """解析 HTML 并提取主要内容。"""
        soup = BeautifulSoup(html, 'html.parser')
        
        for selector in self.REMOVE_SELECTORS:
            for element in soup.select(selector):
                element.decompose()
        
        main_content = None
        for selector in self.CONTENT_SELECTORS:
            main_content = soup.select_one(selector)
            if main_content:
                break
        
        if not main_content:
            main_content = soup.body
        
        if main_content:
            text = main_content.get_text(separator='\n', strip=True)
            text = re.sub(r'\n\s*\n+', '\n\n', text)
            text = re.sub(r' +', ' ', text)
            text = text[:self.max_content_length] if len(text) > self.max_content_length else text
            return text
        
        return None
    
    async def enhance_search_results_async(
        self, 
        results: List[SearchResult],
        max_concurrent: int = 5
    ) -> List[SearchResult]:
        """通过提取完整内容增强搜索结果（使用信号量异步执行）。"""
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def enhance_one(result: SearchResult) -> SearchResult:
            async with semaphore:
                if not result.content:
                    try:
                        content = await self.extract_content_async(result.url)
                        if content:
                            result.content = content
                    except ContentExtractionError as e:
                        logger.warning(f"增强 {result.url} 失败：{e}")
                    except Exception as e:
                        logger.warning(f"增强 {result.url} 时发生未预期错误：{e}")
                return result
        
        try:
            tasks = [enhance_one(result) for result in results]
            return list(await asyncio.gather(*tasks, return_exceptions=False))
        except Exception as e:
            logger.error(f"增强搜索结果出错：{e}")
            return results


# =============================================================================
# 工具函数
# =============================================================================

async def cleanup_http_client() -> None:
    """清理共享 HTTP 客户端，应在应用关闭时调用。"""
    client_manager = HTTPClientManager.get_instance()
    await client_manager.close()
