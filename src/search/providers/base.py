"""Abstract interface implemented by every search provider."""

from abc import ABC, abstractmethod
from typing import List

from src.search.providers.models import ProviderSearchResult


class SearchProvider(ABC):
    """Provider-independent asynchronous search contract."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable provider identifier used by configuration and diagnostics."""

    @abstractmethod
    async def search(
        self,
        query: str,
        max_results: int,
    ) -> List[ProviderSearchResult]:
        """Search for a query or raise a structured provider exception.

        A successful request with no matching documents returns an empty list.
        Provider failures must not be converted into an empty list.
        """
