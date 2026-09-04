from __future__ import annotations

import base64
import binascii
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS
from ddgs.exceptions import DDGSException

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
    (re.compile("月船3号"), "Chandrayaan-3"),
    (re.compile("中国长城"), "Great Wall of China"),
)

DUCKDUCKGO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class SearchProvider:
    def __init__(self, config: AppConfig, timeout: float = 12.0) -> None:
        self.config = config
        self.timeout = timeout
        self.last_errors: list[str] = []

    def search_many(self, queries: list[str], max_results_per_query: int = 5) -> list[SearchResult]:
        self.last_errors = []
        results: list[SearchResult] = []
        active_queries = [query for query in queries if query.strip()]
        if not active_queries:
            return results
        with ThreadPoolExecutor(
            max_workers=min(3, len(active_queries)),
            thread_name_prefix="verity-search",
        ) as executor:
            futures = [
                executor.submit(self.search, query, max_results=max_results_per_query)
                for query in active_queries
            ]
            for future in futures:
                try:
                    results.extend(future.result())
                except SearchProviderError as exc:
                    self.last_errors.append(self._safe_error_message(exc))
        return results

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if self.config.search_provider == "tavily" and self.config.tavily_api_key:
            return tavily_search(query, self.config.tavily_api_key, max_results, self.timeout)
        ddgs_error: SearchProviderError | None = None
        try:
            results = ddgs_search(query, max_results, self.timeout)
            if results:
                return results
        except SearchProviderError as exc:
            ddgs_error = exc

        duckduckgo_error: SearchProviderError | None = None
        try:
            results = duckduckgo_search(query, max_results, self.timeout)
            if results:
                return results
        except SearchProviderError as exc:
            duckduckgo_error = exc

        try:
            return bing_search(query, max_results, self.timeout)
        except SearchProviderError as exc:
            earlier_errors = [error for error in (ddgs_error, duckduckgo_error) if error]
            if earlier_errors:
                details = "; ".join(str(error) for error in earlier_errors)
                raise SearchProviderError(f"{details}; Bing fallback failed: {exc}") from exc
            raise

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
    alias_applied = False
    for pattern, alias in SEARCH_PHRASE_ALIASES:
        normalized_claim, replacement_count = pattern.subn(f" {alias} ", normalized_claim)
        alias_applied = alias_applied or replacement_count > 0
    token_pattern = (
        r"[A-Za-z0-9][A-Za-z0-9'-]*"
        if alias_applied
        else r"[A-Za-z0-9][A-Za-z0-9'-]*|[\u3400-\u4dbf\u4e00-\u9fff]+"
    )
    tokens = re.findall(token_pattern, normalized_claim)
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


def ddgs_search(query: str, max_results: int, timeout: float) -> list[SearchResult]:
    try:
        raw_results = DDGS(timeout=timeout).text(
            query,
            region="us-en",
            safesearch="moderate",
            max_results=max_results,
            backend="auto",
        )
    except DDGSException as exc:
        raise SearchProviderError(f"DDGS metasearch failed: {exc}") from exc

    results: list[SearchResult] = []
    for item in raw_results or []:
        url = item.get("href", "")
        if not url.startswith(("http://", "https://")):
            continue
        results.append(
            SearchResult(
                title=item.get("title", ""),
                url=url,
                snippet=item.get("body", ""),
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
            headers=DUCKDUCKGO_HEADERS,
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


def bing_search(query: str, max_results: int, timeout: float) -> list[SearchResult]:
    try:
        response = httpx.get(
            "https://www.bing.com/search",
            params={"q": query},
            timeout=timeout,
            follow_redirects=True,
            headers=DUCKDUCKGO_HEADERS,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SearchProviderError(f"Bing search failed: {exc}") from exc

    soup = BeautifulSoup(response.text, "html.parser")
    found: list[SearchResult] = []
    for result in soup.select("li.b_algo"):
        link = result.select_one("h2 a[href]")
        if not link:
            continue
        href = normalize_bing_url(link.get("href", ""))
        if not href.startswith(("http://", "https://")):
            continue
        snippet = result.select_one(".b_caption p")
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


def normalize_bing_url(href: str) -> str:
    parsed = urlparse(href)
    if not parsed.netloc.endswith("bing.com") or parsed.path != "/ck/a":
        return href
    encoded = parse_qs(parsed.query).get("u", [""])[0]
    if not encoded.startswith("a1"):
        return href
    payload = encoded[2:]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return href
    return decoded if decoded.startswith(("http://", "https://")) else href


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
