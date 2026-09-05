from __future__ import annotations

import ipaddress
import json
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
from bs4 import BeautifulSoup

from backend.schemas.models import ArticleContent


MAX_RESPONSE_BYTES = 2_000_000
MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = 12.0
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


class URLSafetyError(Exception):
    pass


class ArticleFetchError(Exception):
    pass


@dataclass(frozen=True)
class FetchedPage:
    url: str
    status_code: int
    content_type: str
    text: str
    retrieved_at: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_public_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise URLSafetyError("Only http and https URLs are allowed.")
    if not parsed.hostname:
        raise URLSafetyError("URL must include a hostname.")
    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise URLSafetyError("Localhost and local-network URLs are blocked.")

    for resolved_ip in resolve_hostname(hostname):
        if is_private_or_local_ip(resolved_ip):
            raise URLSafetyError("Private-network, localhost, and link-local URLs are blocked.")
    return url.strip()


def resolve_hostname(hostname: str) -> list[str]:
    try:
        return list({info[4][0] for info in socket.getaddrinfo(hostname, None)})
    except socket.gaierror as exc:
        raise URLSafetyError(f"Could not resolve hostname {hostname!r}.") from exc


def is_private_or_local_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def fetch_page(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> FetchedPage:
    current_url = validate_public_url(url)
    try:
        with httpx.Client(
            follow_redirects=False,
            timeout=timeout,
            headers={"User-Agent": "gonka-ai-fact-checker/0.1"},
        ) as client:
            for redirect_count in range(MAX_REDIRECTS + 1):
                with client.stream("GET", current_url) as response:
                    if response.status_code in REDIRECT_STATUS_CODES:
                        if redirect_count >= MAX_REDIRECTS:
                            raise ArticleFetchError("URL exceeded the maximum redirect count.")
                        location = response.headers.get("location")
                        if not location:
                            raise ArticleFetchError("Redirect response did not include a destination.")
                        current_url = validate_public_url(urljoin(str(response.url), location))
                        continue

                    response.raise_for_status()
                    declared_size = response.headers.get("content-length")
                    if declared_size and declared_size.isdigit() and int(declared_size) > MAX_RESPONSE_BYTES:
                        raise ArticleFetchError("Response exceeded maximum allowed size.")

                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_RESPONSE_BYTES:
                            raise ArticleFetchError("Response exceeded maximum allowed size.")
                        chunks.append(chunk)
                    final_url = str(response.url)
                    status_code = response.status_code
                    content_type = response.headers.get("content-type", "")
                    encoding = response.encoding or "utf-8"
                    break
            else:
                raise ArticleFetchError("URL exceeded the maximum redirect count.")
    except httpx.TimeoutException as exc:
        raise ArticleFetchError("Timeout while fetching URL.") from exc
    except httpx.HTTPStatusError as exc:
        raise ArticleFetchError(f"URL returned HTTP {exc.response.status_code}.") from exc
    except httpx.HTTPError as exc:
        raise ArticleFetchError(f"Could not fetch URL: {exc}") from exc

    body = b"".join(chunks)
    text = body.decode(encoding, errors="replace")
    return FetchedPage(
        url=final_url,
        status_code=status_code,
        content_type=content_type,
        text=text,
        retrieved_at=utc_now_iso(),
    )


def extract_article(url: str) -> ArticleContent:
    page = fetch_page(url)
    extracted = trafilatura.extract(
        page.text,
        include_comments=False,
        include_tables=False,
        no_fallback=False,
        url=page.url,
    )
    title = extract_title(page.text)
    published_date = extract_published_date(page.text)
    text = clean_text(extracted or soup_text(page.text))
    if not text:
        raise ArticleFetchError("No readable article text could be extracted.")
    return ArticleContent(
        url=page.url,
        title=title,
        text=text,
        publisher=urlparse(page.url).hostname or "",
        published_date=published_date,
    )


def extract_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.string:
        return clean_text(soup.title.string)
    h1 = soup.find("h1")
    return clean_text(h1.get_text(" ", strip=True)) if h1 else ""


def extract_published_date(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    meta_keys = {
        "article:published_time",
        "date",
        "datepublished",
        "date_published",
        "publishdate",
        "pubdate",
    }
    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or tag.get("itemprop") or "").strip().lower()
        content = (tag.get("content") or "").strip()
        if key in meta_keys and content:
            return content[:100]

    for tag in soup.find_all("time"):
        value = (tag.get("datetime") or tag.get_text(" ", strip=True)).strip()
        if value:
            return value[:100]

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            value = json.loads(script.string or script.get_text() or "")
        except (TypeError, json.JSONDecodeError):
            continue
        published = find_json_value(value, "datePublished")
        if published:
            return published[:100]
    return ""


def find_json_value(value: Any, key: str) -> str:
    if isinstance(value, dict):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        for nested in value.values():
            found = find_json_value(nested, key)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = find_json_value(nested, key)
            if found:
                return found
    return ""


def soup_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def clean_text(text: str) -> str:
    return " ".join((text or "").split())
