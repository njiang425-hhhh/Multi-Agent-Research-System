"""Tests for legacy SearchResult to Document conversion."""

import pytest

from src.evidence.adapters import search_result_to_document
from src.state import SearchResult


def _search_result(url: str, **overrides: object) -> SearchResult:
    values: dict[str, object] = {
        "query": "LangGraph official documentation",
        "title": "LangGraph documentation",
        "url": url,
        "snippet": "Official framework documentation.",
        "content": "Longer extracted page content.",
    }
    values.update(overrides)
    return SearchResult(**values)


def test_adapter_normalizes_url_scheme_and_host_case_for_stable_identity() -> None:
    upper_case = search_result_to_document(_search_result("HTTPS://EXAMPLE.COM/Guide"))
    lower_case = search_result_to_document(_search_result("https://example.com/Guide"))

    assert upper_case.uri == "https://example.com/Guide"
    assert upper_case.document_id == lower_case.document_id
    assert upper_case.document_id.startswith("web:")


def test_adapter_removes_fragments_from_document_identity() -> None:
    with_fragment = search_result_to_document(
        _search_result("https://example.com/guide#installation")
    )
    without_fragment = search_result_to_document(_search_result("https://example.com/guide"))

    assert with_fragment.uri == "https://example.com/guide"
    assert with_fragment.document_id == without_fragment.document_id
    assert with_fragment.metadata["original_url"] == "https://example.com/guide#installation"


def test_adapter_treats_trailing_slashes_as_the_same_non_root_document() -> None:
    with_slash = search_result_to_document(_search_result("https://example.com/guide/"))
    without_slash = search_result_to_document(_search_result("https://example.com/guide"))

    assert with_slash.uri == "https://example.com/guide"
    assert with_slash.document_id == without_slash.document_id


def test_adapter_generates_the_same_document_id_for_the_same_source() -> None:
    result = _search_result(" https://example.com/guide?version=1 ")

    first_document = search_result_to_document(result)
    second_document = search_result_to_document(result)

    assert first_document.document_id == second_document.document_id
    assert first_document.document_id.startswith("web:")
    assert first_document.uri == "https://example.com/guide?version=1"


def test_adapter_preserves_search_metadata_and_maps_search_result_fields() -> None:
    result = _search_result("https://example.com/guide")

    document = search_result_to_document(result)

    assert document.source_type == "web"
    assert document.title == result.title
    assert document.uri == result.url
    assert document.snippet == result.snippet
    assert document.content == result.content
    assert document.status == "retrieved"
    assert document.metadata == {
        "search_query": result.query,
        "original_url": result.url,
        "normalized_url": result.url,
    }


@pytest.mark.parametrize("content", [None, "", "   "])
def test_adapter_marks_documents_without_usable_content(content: str | None) -> None:
    document = search_result_to_document(
        _search_result("https://example.com/guide", content=content)
    )

    assert document.snippet == "Official framework documentation."
    assert document.content == content
    assert document.status == "content_unavailable"


@pytest.mark.parametrize("url", ["", "not a URL", "ftp://example.com/file"])
def test_adapter_marks_invalid_urls_without_dropping_source_data(url: str) -> None:
    result = _search_result(url)

    document = search_result_to_document(result)

    assert document.document_id.startswith("invalid:")
    assert document.uri == url
    assert document.status == "invalid_source"
    assert document.metadata["original_url"] == url
    assert document.metadata["normalized_url"] is None


def test_adapter_explicitly_binds_an_independent_credibility_record() -> None:
    credibility = {"score": 90, "level": "high", "factors": ["official domain"]}

    document = search_result_to_document(
        _search_result("https://example.com/guide"),
        credibility=credibility,
    )
    credibility["score"] = 0
    credibility["factors"].append("mutated after conversion")

    assert document.credibility == {
        "score": 90,
        "level": "high",
        "factors": ["official domain"],
    }
