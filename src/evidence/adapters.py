"""Explicit adapters between legacy search results and Evidence Layer documents."""

from copy import deepcopy
from hashlib import sha256
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

from src.state import Document, SearchResult


def normalize_web_url(url: str) -> Optional[str]:
    """Return a stable HTTP(S) URL representation, or ``None`` when invalid.

    The normalization is deliberately limited to source identity concerns: it
    trims surrounding whitespace, lowercases scheme and host, removes fragments,
    and treats trailing slashes on non-root paths as equivalent. Query strings
    remain part of the source identity.
    """
    value = url.strip()
    if not value:
        return None

    try:
        parts = urlsplit(value)
    except ValueError:
        return None

    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return None

    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            path,
            parts.query,
            "",
        )
    )


def _stable_document_id(prefix: str, value: str) -> str:
    digest = sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{digest}"


def search_result_to_document(
    result: SearchResult,
    *,
    credibility: Optional[Mapping[str, Any]] = None,
) -> Document:
    """Convert one legacy ``SearchResult`` into the standard ``Document`` type.

    The adapter does not infer credibility.  Callers must explicitly bind the
    credibility record belonging to this search result, which avoids relying on
    implicit State list synchronization.
    """
    normalized_url = normalize_web_url(result.url)
    original_url = result.url
    metadata = {
        "search_query": result.query,
        "original_url": original_url,
        "normalized_url": normalized_url,
    }

    if normalized_url is None:
        return Document(
            document_id=_stable_document_id("invalid", original_url.strip()),
            source_type="web",
            title=result.title,
            uri=original_url,
            snippet=result.snippet,
            content=result.content,
            metadata=metadata,
            credibility=deepcopy(dict(credibility)) if credibility is not None else None,
            status="invalid_source",
        )

    has_content = bool(result.content and result.content.strip())
    return Document(
        document_id=_stable_document_id("web", normalized_url),
        source_type="web",
        title=result.title,
        uri=normalized_url,
        snippet=result.snippet,
        content=result.content,
        metadata=metadata,
        credibility=deepcopy(dict(credibility)) if credibility is not None else None,
        status="retrieved" if has_content else "content_unavailable",
    )


def scored_search_results_to_documents(
    scored_results: Iterable[tuple[SearchResult, Mapping[str, Any]]],
) -> list[Document]:
    """Convert explicitly paired, credibility-filtered results into Documents.

    The caller supplies each ``SearchResult`` together with its own credibility
    record.  This deliberately avoids reconstructing that association from
    separate State lists. Documents are deduplicated by stable identity. The
    first item wins because callers provide credibility-stable order; a later
    duplicate may fill only missing body content.
    """
    documents: list[Document] = []
    positions: dict[str, int] = {}

    for result, credibility in scored_results:
        document = search_result_to_document(result, credibility=credibility)
        existing_index = positions.get(document.document_id)
        if existing_index is not None:
            existing = documents[existing_index]
            if not (existing.content and existing.content.strip()) and (
                document.content and document.content.strip()
            ):
                existing.content = document.content
                if existing.status == "content_unavailable":
                    existing.status = "retrieved"
            search_queries = list(existing.metadata.get("search_queries", []))
            query = document.metadata.get("search_query")
            if query and query not in search_queries:
                search_queries.append(query)
            existing.metadata["search_queries"] = search_queries
            continue
        query = document.metadata.get("search_query")
        document.metadata["search_queries"] = [query] if query else []
        positions[document.document_id] = len(documents)
        documents.append(document)

    return documents
