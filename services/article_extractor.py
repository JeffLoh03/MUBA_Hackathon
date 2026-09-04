from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
import trafilatura
from bs4 import BeautifulSoup

from schemas.models import ArticleContent


MAX_RESPONSE_BYTES = 2_000_000
DEFAULT_TIMEOUT = 12.0


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
    safe_url = validate_public_url(url)
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": "gonka-ai-fact-checker/0.1"},
        ) as client:
            response = client.get(safe_url)
            response.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise ArticleFetchError("Response exceeded maximum allowed size.")
                chunks.append(chunk)
    except httpx.TimeoutException as exc:
        raise ArticleFetchError("Timeout while fetching URL.") from exc
    except httpx.HTTPStatusError as exc:
        raise ArticleFetchError(f"URL returned HTTP {exc.response.status_code}.") from exc
    except httpx.HTTPError as exc:
        raise ArticleFetchError(f"Could not fetch URL: {exc}") from exc

    body = b"".join(chunks)
    encoding = response.encoding or "utf-8"
    text = body.decode(encoding, errors="replace")
    content_type = response.headers.get("content-type", "")
    return FetchedPage(
        url=str(response.url),
        status_code=response.status_code,
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
    text = clean_text(extracted or soup_text(page.text))
    if not text:
        raise ArticleFetchError("No readable article text could be extracted.")
    return ArticleContent(
        url=page.url,
        title=title,
        text=text,
        publisher=urlparse(page.url).hostname or "",
        published_date="",
    )


def extract_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.string:
        return clean_text(soup.title.string)
    h1 = soup.find("h1")
    return clean_text(h1.get_text(" ", strip=True)) if h1 else ""


def soup_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def clean_text(text: str) -> str:
    return " ".join((text or "").split())
