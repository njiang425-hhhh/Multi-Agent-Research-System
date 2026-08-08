"""Provider-layer data models independent from ResearchState."""

from typing import Any, Dict

from pydantic import BaseModel, Field


class ProviderSearchResult(BaseModel):
    """Normalized result returned by a concrete search provider."""

    query: str = Field(description="Query that produced this result")
    title: str = Field(default="", description="Result title")
    url: str = Field(description="Canonical source URL")
    snippet: str = Field(default="", description="Provider result excerpt")
    provider: str = Field(description="Provider that returned the result")
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific metadata not used by the core executor",
    )
