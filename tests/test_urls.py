"""URL minimisation tests (spec sections 20, 35).

These are privacy tests, not formatting tests. A regression here means a query
string reaches the database, and query strings carry search terms, document IDs
and occasionally session tokens.
"""

from __future__ import annotations

import pytest

from focusguard_shared.utilities.urls import (
    coarse_path_prefix,
    minimise_url,
    normalise_domain,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.youtube.com/watch?v=ABC123", "youtube.com"),
        ("https://github.com/user/private-repo", "github.com"),
        ("http://m.facebook.com/", "facebook.com"),
        ("stackoverflow.com", "stackoverflow.com"),
        ("https://DOCS.python.org/3/library/", "docs.python.org"),
        ("https://sub.domain.example.co.uk/path", "sub.domain.example.co.uk"),
        ("https://example.com:8443/app", "example.com"),
    ],
)
def test_normalise_domain(raw: str, expected: str) -> None:
    assert normalise_domain(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "chrome://settings",
        "chrome-extension://abcdef/page.html",
        "about:blank",
        "file:///C:/Users/IC/private/thesis.docx",
        "edge://history",
        "localhost:8000",
        "devtools://devtools/bundled/inspector.html",
    ],
)
def test_untrackable_urls_are_rejected(raw: str) -> None:
    """Browser-internal pages and local files are never recorded."""
    assert normalise_domain(raw) is None
    assert minimise_url(raw) is None


def test_query_string_is_always_discarded() -> None:
    """The core privacy guarantee: no query parameters survive minimisation."""
    url = "https://www.google.com/search?q=how+to+treat+depression&hl=en"
    result = minimise_url(url)

    assert result is not None
    domain, path = result
    assert domain == "google.com"
    assert path == "/search"
    assert "q=" not in str(result)
    assert "depression" not in str(result)


def test_fragment_is_discarded() -> None:
    result = minimise_url("https://example.com/docs#section-private")
    assert result == ("example.com", "/docs")


def test_only_first_path_segment_is_kept() -> None:
    """Deep paths reveal which document was open, so only one segment is stored."""
    assert coarse_path_prefix("https://youtube.com/watch/xyz/extra") == "/watch"
    assert coarse_path_prefix("https://drive.google.com/file/d/SECRET_ID/view") == "/file"


def test_root_url_has_no_path_prefix() -> None:
    assert coarse_path_prefix("https://github.com") is None
    assert coarse_path_prefix("https://github.com/") is None


def test_minimise_url_can_drop_path_entirely() -> None:
    assert minimise_url("https://youtube.com/watch?v=A", keep_path=False) == ("youtube.com", None)


def test_www_and_bare_domain_aggregate_together() -> None:
    """Otherwise a single site would appear as two rows in usage reports."""
    assert normalise_domain("https://www.github.com") == normalise_domain("https://github.com")
