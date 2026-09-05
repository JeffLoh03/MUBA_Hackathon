from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urldefrag

from rapidfuzz import fuzz

from backend.schemas.models import EvidenceItem, SearchResult
from backend.services.article_extractor import ArticleFetchError, URLSafetyError, extract_article
from backend.services.source_ranker import classify_source, publisher_from_url, root_domain


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class EvidenceProcessor:
    def __init__(self, max_evidence: int = 8) -> None:
        self.max_evidence = max_evidence

    def build_evidence(self, search_results: list[SearchResult]) -> list[EvidenceItem]:
        unique_results = dedupe_search_results(search_results)
        fetch_limit = max(self.max_evidence * 2, 8)
        ranked_results = sorted(
            unique_results,
            key=lambda result: classify_source(result.url)[1],
            reverse=True,
        )[:fetch_limit]
        candidates: list[EvidenceItem] = []
        for result in ranked_results:
            evidence = self._result_to_evidence(result)
            if evidence is not None:
                candidates.append(evidence)

        deduped = remove_near_duplicates(candidates)
        ranked = sorted(deduped, key=lambda item: item.source_quality, reverse=True)
        return relabel_evidence(ranked[: self.max_evidence])

    def _result_to_evidence(self, result: SearchResult) -> EvidenceItem | None:
        try:
            article = extract_article(result.url)
            excerpt_source = article.text
            title = article.title or result.title
            url = article.url
            published_date = article.published_date or result.published_date
        except (ArticleFetchError, URLSafetyError):
            excerpt_source = result.raw_content or result.snippet
            title = result.title
            url = result.url
            published_date = result.published_date

        excerpt = normalize_excerpt(excerpt_source)
        if not url or not excerpt:
            return None

        source_type, source_quality = classify_source(url)
        if source_quality < 0.2:
            return None

        return EvidenceItem(
            evidence_id="E0",
            title=title,
            url=url,
            root_domain=root_domain(url),
            publisher=publisher_from_url(url),
            published_date=published_date,
            retrieved_at=utc_now_iso(),
            excerpt=excerpt,
            source_type=source_type,
            source_quality=source_quality,
        )


def dedupe_search_results(results: list[SearchResult]) -> list[SearchResult]:
    seen: set[str] = set()
    unique: list[SearchResult] = []
    for result in results:
        normalized = normalize_url(result.url)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(result)
    return unique


def normalize_url(url: str) -> str:
    return urldefrag(url.strip())[0].rstrip("/")


def remove_near_duplicates(items: list[EvidenceItem]) -> list[EvidenceItem]:
    kept: list[EvidenceItem] = []
    for item in sorted(items, key=lambda value: value.source_quality, reverse=True):
        duplicate_index = find_duplicate_index(kept, item)
        if duplicate_index is None:
            kept.append(item)
            continue
        if item.source_quality > kept[duplicate_index].source_quality:
            kept[duplicate_index] = item
    return kept


def find_duplicate_index(items: list[EvidenceItem], item: EvidenceItem) -> int | None:
    for index, existing in enumerate(items):
        same_domain = existing.root_domain == item.root_domain and existing.root_domain
        title_match = fuzz.token_set_ratio(existing.title, item.title) >= 94 if item.title else False
        excerpt_match = fuzz.token_set_ratio(existing.excerpt, item.excerpt) >= 92
        if normalize_url(existing.url) == normalize_url(item.url):
            return index
        if same_domain and (title_match or excerpt_match):
            return index
        if title_match and excerpt_match:
            return index
    return None


def relabel_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    relabelled: list[EvidenceItem] = []
    for index, item in enumerate(items, start=1):
        relabelled.append(item.model_copy(update={"evidence_id": f"E{index}"}))
    return relabelled


def normalize_excerpt(text: str, max_chars: int = 1200) -> str:
    clean = " ".join((text or "").split())
    return clean[:max_chars]


def split_evidence_by_model_outputs(
    all_evidence: list[EvidenceItem],
    supporting_ids: set[str],
    contradicting_ids: set[str],
) -> tuple[list[EvidenceItem], list[EvidenceItem]]:
    by_id = {item.evidence_id: item for item in all_evidence}
    supporting = [by_id[item_id] for item_id in sorted(supporting_ids) if item_id in by_id]
    contradicting = [by_id[item_id] for item_id in sorted(contradicting_ids) if item_id in by_id]
    return supporting, contradicting
