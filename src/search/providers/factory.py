"""Factory infrastructure for selecting registered search providers."""

from collections.abc import Callable, Mapping
from typing import Any, Dict, Optional

from src.search.providers.base import SearchProvider
from src.search.providers.duckduckgo import DuckDuckGoProvider
from src.search.providers.errors import UnavailableError
from src.search.providers.tavily import TavilyProvider


ProviderBuilder = Callable[..., SearchProvider]
SUPPORTED_PROVIDERS = frozenset({"tavily", "duckduckgo"})
DEFAULT_BUILDERS: Mapping[str, ProviderBuilder] = {
    "tavily": TavilyProvider,
    "duckduckgo": DuckDuckGoProvider,
}


class SearchProviderFactory:
    """Select and construct a registered provider by configuration name.

    Every supported provider is registered through this one factory. Provider
    selection is deliberately independent from retry/fallback policy.
    """

    def __init__(
        self,
        builders: Optional[Mapping[str, ProviderBuilder]] = None,
    ) -> None:
        self._builders: Dict[str, ProviderBuilder] = {}
        for name, builder in DEFAULT_BUILDERS.items():
            self.register(name, builder)
        for name, builder in (builders or {}).items():
            self.register(name, builder)

    @property
    def supported_providers(self) -> frozenset[str]:
        return SUPPORTED_PROVIDERS

    @property
    def registered_providers(self) -> frozenset[str]:
        return frozenset(self._builders)

    def register(self, provider_name: str, builder: ProviderBuilder) -> None:
        """Register a concrete provider constructor for a supported name."""

        normalized_name = self._normalize_name(provider_name)
        if not callable(builder):
            raise TypeError("provider builder must be callable")
        self._builders[normalized_name] = builder

    def create(self, provider_name: str, **kwargs: Any) -> SearchProvider:
        """Create the selected provider or raise a structured error."""

        normalized_name = self._normalize_name(provider_name)
        builder = self._builders.get(normalized_name)
        if builder is None:
            raise UnavailableError(
                "Provider implementation has not been registered",
                provider=normalized_name,
                retryable=False,
            )

        provider = builder(**kwargs)
        if not isinstance(provider, SearchProvider):
            raise TypeError(
                f"Builder for '{normalized_name}' must return SearchProvider"
            )
        if provider.name != normalized_name:
            raise ValueError(
                "Provider name does not match the factory registration: "
                f"expected '{normalized_name}', got '{provider.name}'"
            )
        return provider

    @staticmethod
    def _normalize_name(provider_name: str) -> str:
        normalized_name = provider_name.strip().lower()
        if normalized_name not in SUPPORTED_PROVIDERS:
            supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
            raise ValueError(
                f"Unsupported search provider '{provider_name}'. "
                f"Supported providers: {supported}"
            )
        return normalized_name
