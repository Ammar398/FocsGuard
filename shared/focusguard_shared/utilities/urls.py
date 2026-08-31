"""URL minimisation (spec sections 20, 35).

FocusGuard records *where* someone spent time, never *what* they read. A URL such
as::

    https://www.youtube.com/watch?v=ABC123&list=PRIVATE#t=42

is reduced to a domain and at most a coarse first path segment::

    youtube.com  +  /watch

Query strings and fragments are discarded outright: they routinely carry search
terms, document identifiers, session tokens and email addresses, none of which a
manager needs in order to see that 45 minutes went to YouTube.

The browser extension applies this before transmitting, so the full URL never
crosses a process boundary. These functions are duplicated in JavaScript inside
the extension; the tests in tests/test_urls.py pin the behaviour both must share.
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: Leading labels stripped so that "www.github.com" and "github.com" aggregate
#: into a single row rather than appearing as two separate sites.
_STRIPPED_PREFIXES: tuple[str, ...] = ("www.", "m.", "mobile.")

#: Schemes that identify browser-internal pages rather than websites. They are
#: not tracked at all - a user's local file paths and extension IDs are not our
#: business.
_IGNORED_SCHEMES: frozenset[str] = frozenset(
    {"about", "chrome", "chrome-extension", "edge", "extension", "file", "devtools", "view-source"}
)


def normalise_domain(url_or_host: str) -> str | None:
    """Return the bare, lower-cased registrable host, or None if untrackable.

    Accepts either a full URL or a bare hostname, because the extension supplies
    URLs while configuration files tend to list hostnames.
    """
    if not url_or_host or not url_or_host.strip():
        return None

    candidate = url_or_host.strip()

    # urlsplit only recognises a scheme when "//" is present; adding a synthetic
    # one lets a bare "github.com/foo" parse the same way as a full URL.
    if "://" not in candidate:
        if candidate.split("/", 1)[0].split(":", 1)[0] in _IGNORED_SCHEMES:
            return None
        candidate = f"https://{candidate}"

    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None

    if parts.scheme in _IGNORED_SCHEMES:
        return None

    host = (parts.hostname or "").lower().strip(".")
    if not host or "." not in host:
        # Rejects "localhost" and malformed hosts. Local development servers are
        # tracked through the application layer instead.
        return None

    for prefix in _STRIPPED_PREFIXES:
        if host.startswith(prefix) and len(host) > len(prefix):
            host = host[len(prefix) :]
            break

    return host


def coarse_path_prefix(url: str, max_length: int = 64) -> str | None:
    """Return only the first path segment, e.g. "/watch" - or None.

    One segment is enough to distinguish "youtube.com/watch" from
    "youtube.com/results" while revealing nothing about the specific video,
    document or profile being viewed.
    """
    if "://" not in url:
        url = f"https://{url}"

    try:
        parts = urlsplit(url)
    except ValueError:
        return None

    if parts.scheme in _IGNORED_SCHEMES:
        return None

    segments = [segment for segment in parts.path.split("/") if segment]
    if not segments:
        return None

    first = segments[0][:max_length]
    return f"/{first}"


def minimise_url(url: str, keep_path: bool = True) -> tuple[str, str | None] | None:
    """Reduce a URL to ``(domain, path_prefix)``, discarding everything else.

    Returns None for URLs that must not be tracked at all, such as ``file://``
    and ``chrome-extension://``.
    """
    domain = normalise_domain(url)
    if domain is None:
        return None

    path = coarse_path_prefix(url) if keep_path else None
    return domain, path
