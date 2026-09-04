from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from config import AppConfig
from schemas.models import SearchQueries, SearchResult


class SearchProviderError(Exception):
    pass


SEARCH_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "by",
    "claim",
    "claimed",
    "claims",
    "confirm",
    "confirmed",
    "confirms",
    "for",
    "from",
    "had",
    "has",
    "have",
    "in",
    "is",
    "it",
    "its",
    "made",
    "make",
    "makes",
    "of",
    "on",
    "or",
    "said",
    "says",
    "that",
    "the",
    "their",
    "this",
    "to",
    "was",
    "were",
    "with",
}

SEARCH_PHRASE_ALIASES = (
    (re.compile(r"\bworld health organization\b", re.IGNORECASE), "WHO"),
    (re.compile(r"\bnational aeronautics and space administration\b", re.IGNORECASE), "NASA"),
    (re.compile("美国国家航空航天局"), "NASA"),
)


class SearchProvider:
    def __init__(self, config: AppConfig, timeout: float = 12.0) -> None:
        self.config = config
        self.timeout = timeout
        self.last_errors: list[str] = []

    def search_many(self, queries: list[str], max_results_per_query: int = 5) -> list[SearchResult]:
        self.last_errors = []
        results: list[SearchResult] = []
        for query in queries:
            if not query.strip():
                continue
            try:
                results.extend(self.search(query, max_results=max_results_per_query))
            except SearchProviderError as exc:
                self.last_errors.append(self._safe_error_message(exc))
                continue
        return results

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if self.config.search_provider == "tavily" and self.config.tavily_api_key:
            return tavily_search(query, self.config.tavily_api_key, max_results, self.timeout)
        return duckduckgo_search(query, max_results, self.timeout)

    def _safe_error_message(self, exc: Exception) -> str:
        message = str(exc)
        if self.config.tavily_api_key:
            message = message.replace(self.config.tavily_api_key, "[REDACTED]")
        return message[:500]


def deterministic_search_queries(claim: str) -> SearchQueries:
    compact = " ".join(claim.split())
    search_terms = extract_search_terms(compact)
    quoted = f'"{compact[:160]}"'
    return SearchQueries(
        general_query=search_terms,
        official_source_query=f"{search_terms} official",
        supporting_evidence_query=f"{search_terms} evidence report",
        contradicting_evidence_query=f"{search_terms} false denial correction no threat",
        date_context_query=f"{search_terms} date location context",
        old_news_or_misinformation_query=f"{quoted} old news misinformation",
    )


def extract_search_terms(claim: str, max_terms: int = 8) -> str:
    normalized_claim = claim
    for pattern, alias in SEARCH_PHRASE_ALIASES:
        normalized_claim = pattern.sub(alias, normalized_claim)
    tokens = re.findall(r"\b[\w'-]+\b", normalized_claim, flags=re.UNICODE)
    useful = [token for token in tokens if token.lower() not in SEARCH_STOP_WORDS]
    if not useful:
        return " ".join(claim.split())[:200]
    return " ".join(useful[:max_terms])


def tavily_search(
    query: str,
    api_key: str,
    max_results: int,
    timeout: float,
) -> list[SearchResult]:
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "include_answer": False,
        "include_raw_content": True,
    }
    try:
        response = httpx.post("https://api.tavily.com/search", json=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        raise SearchProviderError(f"Tavily raw search failed: {exc}") from exc

    results = []
    for item in data.get("results", [])[:max_results]:
        url = item.get("url")
        if not url:
            continue
        results.append(
            SearchResult(
                title=item.get("title", ""),
                url=url,
                snippet=item.get("content", ""),
                published_date=item.get("published_date", ""),
                raw_content=item.get("raw_content") or "",
            )
        )
    return results


def duckduckgo_search(query: str, max_results: int, timeout: float) -> list[SearchResult]:
    try:
        response = httpx.get(
            "https://duckduckgo.com/html/",
            params={"q": query},
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "gonka-ai-fact-checker/0.1"},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SearchProviderError(f"DuckDuckGo search failed: {exc}") from exc

    soup = BeautifulSoup(response.text, "html.parser")
    found: list[SearchResult] = []
    for result in soup.select(".result"):
        link = result.select_one(".result__a")
        if not link:
            continue
        href = normalize_duckduckgo_url(link.get("href", ""))
        if not href:
            continue
        snippet = result.select_one(".result__snippet")
        found.append(
            SearchResult(
                title=link.get_text(" ", strip=True),
                url=href,
                snippet=snippet.get_text(" ", strip=True) if snippet else "",
            )
        )
        if len(found) >= max_results:
            break
    return found


def normalize_duckduckgo_url(href: str) -> str:
    if not href:
        return ""
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path == "/l/":
        return unquote(parse_qs(parsed.query).get("uddg", [""])[0])
    if href.startswith("//duckduckgo.com/l/"):
        parsed = urlparse("https:" + href)
        return unquote(parse_qs(parsed.query).get("uddg", [""])[0])
    return href
