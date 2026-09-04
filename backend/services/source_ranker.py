from __future__ import annotations

from urllib.parse import urlparse

import tldextract


ESTABLISHED_NEWS = {
    "apnews.com",
    "reuters.com",
    "bbc.com",
    "bbc.co.uk",
    "npr.org",
    "theguardian.com",
    "nytimes.com",
    "washingtonpost.com",
    "wsj.com",
    "ft.com",
    "aljazeera.com",
    "cnn.com",
}

SPECIALIST_DOMAINS = {
    "snopes.com",
    "politifact.com",
    "factcheck.org",
    "fullfact.org",
    "healthline.com",
    "nature.com",
    "science.org",
    "thelancet.com",
}


def root_domain(url: str) -> str:
    parsed = urlparse(url)
    extracted = tldextract.extract(parsed.hostname or "")
    if not extracted.domain or not extracted.suffix:
        return parsed.hostname or ""
    return f"{extracted.domain}.{extracted.suffix}".lower()


def publisher_from_url(url: str) -> str:
    domain = root_domain(url)
    return domain or (urlparse(url).hostname or "")


def classify_source(url: str) -> tuple[str, float]:
    domain = root_domain(url)
    parsed_host = (urlparse(url).hostname or "").lower()

    if parsed_host.endswith(".gov") or ".gov." in parsed_host:
        return "official_government", 0.95
    if parsed_host.endswith(".edu") or ".edu." in parsed_host:
        return "academic_institution", 0.9
    if parsed_host.endswith(".int") or domain in {"who.int", "un.org", "worldbank.org"}:
        return "official_institution", 0.9
    if domain in ESTABLISHED_NEWS:
        return "established_news", 0.78
    if domain in SPECIALIST_DOMAINS:
        return "specialist_source", 0.74
    if domain.endswith("wikipedia.org"):
        return "reference", 0.45
    if any(name in domain for name in ("facebook", "instagram", "x.com", "twitter", "tiktok")):
        return "social_media", 0.2
    if any(name in domain for name in ("blog", "medium.com", "substack.com")):
        return "blog", 0.35
    return "web_source", 0.5
