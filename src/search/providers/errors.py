"""Structured exceptions raised by search providers."""

from typing import Any, Dict, Optional


class SearchProviderError(Exception):
    """Base exception for failures reported by a search provider."""

    code = "search_provider_error"
    default_retryable = False

    def __init__(
        self,
        message: str,
        *,
        provider: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        retryable: Optional[bool] = None,
        retry_after_seconds: Optional[float] = None,
    ) -> None:
        self.message = message
        self.provider = provider
        self.details = details or {}
        self.retryable = self.default_retryable if retryable is None else retryable
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)

    def __str__(self) -> str:
        provider = f"[{self.provider}] " if self.provider else ""
        return f"[{self.code}] {provider}{self.message}"

    def to_dict(self) -> Dict[str, Any]:
        """Return a serializable representation for logs and future traces."""

        return {
            "code": self.code,
            "message": self.message,
            "provider": self.provider,
            "retryable": self.retryable,
            "retry_after_seconds": self.retry_after_seconds,
            "details": self.details,
        }


class TimeoutError(SearchProviderError):
    """The provider did not respond within its request deadline."""

    code = "provider_timeout"
    default_retryable = True


class AuthError(SearchProviderError):
    """Provider credentials are missing, invalid, or unauthorized."""

    code = "provider_auth_error"
    default_retryable = False


class RateLimitError(SearchProviderError):
    """The provider rejected the request because of rate limiting."""

    code = "provider_rate_limit"
    default_retryable = True


class UnavailableError(SearchProviderError):
    """The provider is temporarily unavailable or not configured."""

    code = "provider_unavailable"
    default_retryable = True


class CircuitOpenError(SearchProviderError):
    """The provider circuit breaker is open."""

    code = "provider_circuit_open"
    default_retryable = True
