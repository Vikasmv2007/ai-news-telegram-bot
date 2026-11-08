"""Text helpers: HTML stripping, URL canonicalisation, title fingerprints."""
from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "cmpid", "ito", "from", "src",
    "ocid", "smid", "taid", "yptr", "guccounter", "rss", "feed",
}
STOPWORDS = set(
    """a an the of to in on for and or but with at by from as is are was were be been be's it its this that these
    those after before over under into about amid says said say will would could may might can new news live update
    updates today report reports vs via up down how why what who when where than more most after""".split()
)


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:-") + "…"


def canonical_url(url: str) -> str:
    """Normalise a URL so the same article from different links maps to one key."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    scheme = "https" if parts.scheme in ("http", "https") else parts.scheme
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m.") or host.startswith("amp."):
        host = host.split(".", 1)[1]
    path = re.sub(r"/amp/?$", "", parts.path) or "/"
    path = re.sub(r"\.amp(\.html?)?$", r"\1", path)
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
             if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith("utm_")]
    return urlunsplit((scheme, host, path, urlencode(sorted(query)), ""))


def article_id_for(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode("utf-8")).hexdigest()[:20]


def clean_title(title: str, source: str | None = None) -> str:
    """Remove the ' - Publisher' suffix Google News appends, and tidy whitespace."""
    t = strip_html(title)
    if source:
        for sep in (" - ", " | ", " — "):
            suffix = f"{sep}{source}"
            if t.endswith(suffix):
                t = t[: -len(suffix)]
    return t.strip()


def title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", title.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 1}


def title_fingerprint(title: str) -> str:
    toks = sorted(title_tokens(title))
    return hashlib.sha1(" ".join(toks).encode()).hexdigest()[:16]


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def first_sentences(text: str, n: int = 3, max_chars: int = 600) -> str:
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text.strip())
    out = " ".join(s for s in sentences[:n] if s)
    return truncate(out, max_chars)
