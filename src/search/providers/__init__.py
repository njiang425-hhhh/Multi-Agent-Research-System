"""Search provider contracts and factory infrastructure."""

from src.search.providers.base import SearchProvider
from src.search.providers.errors import (
    AuthError,
    CircuitOpenError,
    RateLimitError,
    SearchProviderError,
    TimeoutError,
    UnavailableError,
)
from src.search.providers.factory import SearchProviderFactory
from src.search.providers.models import ProviderSearchResult

__all__ = [
    "AuthError",
    "CircuitOpenError",
    "ProviderSearchResult",
    "RateLimitError",
    "SearchProvider",
    "SearchProviderError",
    "SearchProviderFactory",
    "TimeoutError",
    "UnavailableError",
]
