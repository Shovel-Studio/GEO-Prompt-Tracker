"""URL/domain helpers and source de-duplication."""
from __future__ import annotations

from urllib.parse import urlparse, parse_qs, unquote

from .engines.base import Source

# Params to strip so the same article from different runs collapses to one URL.
_TRACKING_PREFIXES = ("utm_", "ref", "fbclid", "gclid")


def get_domain(url: str) -> str:
    if not url:
        return ""
    value = url.strip()
    if "://" not in value:
        value = "https://" + value
    try:
        host = urlparse(value).hostname or ""
    except ValueError:
        return ""
    return host.lower().removeprefix("www.").rstrip(".")


def clean_url(url: str) -> str:
    """Drop tracking params and fragments; unwrap common redirectors."""
    if not url:
        return ""
    value = url.strip().split("#", 1)[0]
    if "://" not in value:
        value = "https://" + value
    try:
        parsed = urlparse(value)
    except ValueError:
        return value
    # Unwrap Google/Vertex grounding redirects that carry the real URL in a param.
    if parsed.hostname and ("google.com/url" in value or "vertexaisearch" in parsed.hostname):
        qs = parse_qs(parsed.query)
        for key in ("url", "q", "u"):
            if qs.get(key):
                return clean_url(unquote(qs[key][0]))
    kept = [
        p for p in parsed.query.split("&")
        if p and not any(p.lower().startswith(t) for t in _TRACKING_PREFIXES)
    ]
    query = ("?" + "&".join(kept)) if kept else ""
    return f"{parsed.scheme}://{parsed.hostname}{parsed.path}{query}".rstrip("/")


def is_citation(url: str) -> bool:
    """False for links that aren't cited sources, e.g. the Google Shopping
    product cards Gemini renders (google.com/search?...&ibp=oshop)."""
    parsed = urlparse(url)
    return not (get_domain(url) == "google.com" and parsed.path.startswith("/search"))


def normalize_sources(raw: list[Source]) -> list[Source]:
    """Clean URLs, fill domains, and de-dupe by cleaned URL preserving order."""
    seen: set[str] = set()
    out: list[Source] = []
    for s in raw:
        url = clean_url(s.url)
        if not url or url in seen or not is_citation(url):
            continue
        seen.add(url)
        out.append(Source(url=url, title=s.title.strip(), domain=get_domain(url)))
    return out
