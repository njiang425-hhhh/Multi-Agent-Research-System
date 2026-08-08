"""深度研究代理的自定义异常。"""

from typing import Optional


class DeepResearchError(Exception):
    """所有研究错误的基类异常。"""
    
    def __init__(self, message: str, details: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.details = details
    
    def __str__(self) -> str:
        if self.details:
            return f"{self.message}: {self.details}"
        return self.message


class ConfigurationError(DeepResearchError):
    """配置或环境错误。"""
    pass


class PlanningError(DeepResearchError):
    """研究规划阶段的错误。"""
    pass


class SearchError(DeepResearchError):
    """网络搜索阶段的错误。"""
    pass


class RateLimitError(SearchError):
    """外部 API 超出速率限制。"""
    
    def __init__(
        self, 
        message: str = "超出速率限制",
        retry_after: int = 60,
        service: str = "unknown"
    ):
        super().__init__(message)
        self.retry_after = retry_after
        self.service = service


class ContentExtractionError(DeepResearchError):
    """无法从 URL 提取内容。"""
    
    def __init__(self, message: str, url: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.url = url
        self.status_code = status_code


class SynthesisError(DeepResearchError):
    """研究发现综合阶段的错误。"""
    pass


class ReportGenerationError(DeepResearchError):
    """报告生成阶段的错误。"""
    pass


class CircuitOpenError(DeepResearchError):
    """熔断器已打开，服务暂时不可用。"""
    
    def __init__(self, service: str, retry_after: float):
        super().__init__(f"服务 {service} 的熔断器已打开")
        self.service = service
        self.retry_after = retry_after


class ValidationError(DeepResearchError):
    """数据验证错误。"""
    pass


class LLMError(DeepResearchError):
    """LLM 提供商错误。"""
    
    def __init__(
        self, 
        message: str, 
        provider: str,
        model: Optional[str] = None,
        is_retryable: bool = True
    ):
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.is_retryable = is_retryable
