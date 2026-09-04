from __future__ import annotations

from collections import Counter

from schemas.models import EvidenceItem, SourceCredibilityAssessment


HIGH_QUALITY_THRESHOLD = 0.7
LOW_QUALITY_THRESHOLD = 0.35


def assess_source_credibility(evidence: list[EvidenceItem]) -> SourceCredibilityAssessment:
    if not evidence:
        return SourceCredibilityAssessment(
            source_trust_score=0,
            website_risk_level="Unknown",
            duplicate_or_syndication_risk="Unknown",
            risk_signals=["No validated evidence sources were retrieved."],
            summary="No source credibility assessment is possible without retrieved evidence.",
        )

    domains = [item.root_domain or item.publisher or item.url for item in evidence]
    domain_counts = Counter(domains)
    independent_count = len(domain_counts)
    high_quality = [item for item in evidence if item.source_quality >= HIGH_QUALITY_THRESHOLD]
    official = [item for item in evidence if item.source_type.startswith("official") or "institution" in item.source_type]
    low_quality = [item for item in evidence if item.source_quality <= LOW_QUALITY_THRESHOLD]
    missing_date_count = sum(1 for item in evidence if not item.published_date)
    average_quality = sum(item.source_quality for item in evidence) / len(evidence)
    best_quality = max(item.source_quality for item in evidence)

    score = round(
        average_quality * 52
        + best_quality * 20
        + min(independent_count, 4) * 6
        + min(len(high_quality), 3) * 5
        + min(len(official), 2) * 5
        - len(low_quality) * 5
        - min(missing_date_count, 4) * 2
    )
    if independent_count <= 1 and not official:
        score -= 15
    if len(evidence) >= 3 and independent_count == 1:
        score -= 10
    score = clamp(score)

    duplicate_risk = duplicate_or_syndication_risk(len(evidence), independent_count)
    risk_level = risk_level_for_score(score, independent_count, len(high_quality), len(official))
    trust_signals = build_trust_signals(independent_count, high_quality, official, best_quality)
    risk_signals = build_risk_signals(
        evidence=evidence,
        independent_count=independent_count,
        low_quality_count=len(low_quality),
        missing_date_count=missing_date_count,
        duplicate_risk=duplicate_risk,
    )

    strongest_sources = [
        f"{item.evidence_id}: {item.publisher or item.root_domain} ({item.source_type}, {item.source_quality:.2f})"
        for item in sorted(evidence, key=lambda value: value.source_quality, reverse=True)[:3]
    ]

    return SourceCredibilityAssessment(
        source_trust_score=score,
        website_risk_level=risk_level,
        independent_source_count=independent_count,
        high_quality_source_count=len(high_quality),
        official_source_count=len(official),
        missing_date_count=missing_date_count,
        duplicate_or_syndication_risk=duplicate_risk,
        strongest_sources=strongest_sources,
        trust_signals=trust_signals,
        risk_signals=risk_signals,
        summary=build_summary(score, risk_level, independent_count, len(high_quality), duplicate_risk),
    )


def duplicate_or_syndication_risk(evidence_count: int, independent_count: int) -> str:
    if evidence_count == 0:
        return "Unknown"
    if independent_count == 1 and evidence_count > 1:
        return "High"
    if independent_count <= max(1, evidence_count // 2):
        return "Medium"
    return "Low"


def risk_level_for_score(
    score: int,
    independent_count: int,
    high_quality_count: int,
    official_count: int,
) -> str:
    if score >= 75 and independent_count >= 2:
        return "Low"
    if official_count > 0 and high_quality_count > 0 and score >= 65:
        return "Low"
    if score >= 50:
        return "Medium"
    return "High"


def build_trust_signals(
    independent_count: int,
    high_quality: list[EvidenceItem],
    official: list[EvidenceItem],
    best_quality: float,
) -> list[str]:
    signals: list[str] = []
    if independent_count >= 2:
        signals.append(f"Evidence appears across {independent_count} independent root domains.")
    if official:
        signals.append("At least one official or institutional source is present.")
    if high_quality:
        signals.append(f"{len(high_quality)} high-quality source(s) were found.")
    if best_quality >= 0.75:
        signals.append("The strongest evidence comes from an established or primary source.")
    return signals or ["No strong positive source-quality signal was found."]


def build_risk_signals(
    *,
    evidence: list[EvidenceItem],
    independent_count: int,
    low_quality_count: int,
    missing_date_count: int,
    duplicate_risk: str,
) -> list[str]:
    signals: list[str] = []
    if independent_count <= 1:
        signals.append("The claim is not corroborated across multiple independent root domains.")
    if low_quality_count:
        signals.append(f"{low_quality_count} low-quality or social/blog source(s) were found.")
    if missing_date_count:
        signals.append(f"{missing_date_count} evidence item(s) lack a publication date.")
    if duplicate_risk in {"Medium", "High"}:
        signals.append(f"Duplicate or syndicated-source risk is {duplicate_risk.lower()}.")
    if any(item.source_type == "social_media" for item in evidence):
        signals.append("Social-media evidence is treated as low confidence unless backed by primary evidence.")
    return signals or ["No major source-risk signal was found."]


def build_summary(
    score: int,
    risk_level: str,
    independent_count: int,
    high_quality_count: int,
    duplicate_risk: str,
) -> str:
    return (
        f"Source trust is {score}/100 with {risk_level.lower()} website/source risk. "
        f"The assessment found {independent_count} independent source domain(s), "
        f"{high_quality_count} high-quality source(s), and {duplicate_risk.lower()} duplicate/syndication risk."
    )


def clamp(value: int) -> int:
    return max(0, min(100, value))
