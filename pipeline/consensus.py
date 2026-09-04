from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from schemas.models import EvidenceItem, SourceCredibilityAssessment, VerifierOutput


VERDICT_ORDER = {
    "false": 0,
    "mostly_false": 1,
    "misleading": 2,
    "mostly_true": 3,
    "true": 4,
    "unverified": 2,
}


@dataclass(frozen=True)
class ConsensusResult:
    final_verdict: str
    truth_score: int
    confidence_score: int
    concise_explanation: str
    used_judge: bool = False


def needs_judge(verifier_1: VerifierOutput, verifier_2: VerifierOutput) -> bool:
    if "unverified" in {verifier_1.verdict, verifier_2.verdict}:
        return False
    score_gap = abs(verifier_1.support_score - verifier_2.support_score)
    verdict_gap = abs(VERDICT_ORDER[verifier_1.verdict] - VERDICT_ORDER[verifier_2.verdict])
    return score_gap > 25 or verdict_gap >= 2


def build_consensus(
    verifiers: list[VerifierOutput],
    evidence: list[EvidenceItem],
    *,
    judge_output: VerifierOutput | None = None,
    minimum_evidence_quality: float = 0.35,
    source_credibility: SourceCredibilityAssessment | None = None,
    insufficient_evidence_explanation: str | None = None,
    expected_verifier_count: int | None = None,
) -> ConsensusResult:
    if not has_sufficient_reliable_evidence(evidence, minimum_evidence_quality):
        return ConsensusResult(
            final_verdict="Unverified",
            truth_score=50,
            confidence_score=25,
            concise_explanation=insufficient_evidence_explanation
            or "Reliable evidence was insufficient, so the claim remains unverified.",
            used_judge=judge_output is not None,
        )

    if judge_output is not None:
        score = clamp_score(judge_output.support_score)
        verdict = category_for_score(score)
        if judge_output.verdict == "unverified":
            verdict = "Unverified"
        elif judge_output.context_mismatch:
            verdict = "Misleading"
        confidence = adjusted_confidence([*verifiers, judge_output], agreement_bonus=False)
        confidence = clamp_score(
            confidence - unavailable_verifier_penalty(verifiers, expected_verifier_count)
        )
        result = ConsensusResult(
            final_verdict=verdict,
            truth_score=score,
            confidence_score=confidence,
            concise_explanation=judge_output.reasoning_summary
            or "A judge model resolved a material disagreement using the supplied evidence.",
            used_judge=True,
        )
        return apply_source_credibility_adjustment(result, source_credibility)

    if not verifiers:
        return ConsensusResult(
            final_verdict="Unverified",
            truth_score=50,
            confidence_score=20,
            concise_explanation="No verifier output was available.",
        )

    unverified_count = sum(item.verdict == "unverified" for item in verifiers)
    if unverified_count > len(verifiers) // 2:
        return apply_source_credibility_adjustment(
            ConsensusResult(
                final_verdict="Unverified",
                truth_score=50,
                confidence_score=min(adjusted_confidence(verifiers, agreement_bonus=False), 40),
                concise_explanation=combine_reasoning_summaries(verifiers),
            ),
            source_credibility,
        )

    decisive_verifiers = [item for item in verifiers if item.verdict != "unverified"]
    required_decisive_count = 2 if (expected_verifier_count or 0) >= 2 else 1
    if len(decisive_verifiers) < required_decisive_count:
        return apply_source_credibility_adjustment(
            ConsensusResult(
                final_verdict="Unverified",
                truth_score=50,
                confidence_score=min(
                    adjusted_confidence(verifiers, agreement_bonus=False),
                    35,
                ),
                concise_explanation=(
                    "At least two decisive verifier outputs are required for a firm verdict."
                ),
            ),
            source_credibility,
        )

    score = robust_support_score(decisive_verifiers)
    verdict = category_for_score(score)
    context_mismatch_count = sum(item.context_mismatch for item in decisive_verifiers)
    if context_mismatch_count > len(decisive_verifiers) // 2:
        verdict = "Misleading"
    confidence = adjusted_confidence(decisive_verifiers, agreement_bonus=True)
    confidence -= unverified_count * 8
    confidence -= unavailable_verifier_penalty(verifiers, expected_verifier_count)
    confidence = clamp_score(confidence)
    explanation = combine_reasoning_summaries(decisive_verifiers)
    result = ConsensusResult(
        final_verdict=verdict,
        truth_score=score,
        confidence_score=confidence,
        concise_explanation=explanation,
    )
    return apply_source_credibility_adjustment(result, source_credibility)


def has_sufficient_reliable_evidence(
    evidence: list[EvidenceItem],
    minimum_evidence_quality: float,
) -> bool:
    if not evidence:
        return False
    best_quality = max(item.source_quality for item in evidence)
    average_quality = sum(item.source_quality for item in evidence) / len(evidence)
    return best_quality >= 0.5 or average_quality >= minimum_evidence_quality


def weighted_support_score(verifiers: list[VerifierOutput]) -> int:
    total_weight = sum(max(item.confidence, 1) for item in verifiers)
    weighted = sum(item.support_score * max(item.confidence, 1) for item in verifiers)
    return clamp_score(round(weighted / total_weight))


def robust_support_score(verifiers: list[VerifierOutput]) -> int:
    if len(verifiers) < 3:
        return weighted_support_score(verifiers)
    return clamp_score(round(median(item.support_score for item in verifiers)))


def unavailable_verifier_penalty(
    verifiers: list[VerifierOutput],
    expected_verifier_count: int | None,
) -> int:
    if expected_verifier_count is None:
        return 0
    unavailable_count = max(expected_verifier_count - len(verifiers), 0)
    return unavailable_count * 10


def adjusted_confidence(verifiers: list[VerifierOutput], *, agreement_bonus: bool) -> int:
    if not verifiers:
        return 0
    base = round(sum(item.confidence for item in verifiers) / len(verifiers))
    if len(verifiers) == 1:
        return clamp_score(min(base, 70))
    if agreement_bonus and len(verifiers) >= 2:
        score_gap = max(item.support_score for item in verifiers) - min(
            item.support_score for item in verifiers
        )
        verdict_counts = {
            verdict: sum(item.verdict == verdict for item in verifiers)
            for verdict in {item.verdict for item in verifiers}
        }
        largest_group = max(verdict_counts.values())
        if score_gap <= 10 and largest_group == len(verifiers):
            base += 8
        elif score_gap <= 25:
            base += 3
        elif score_gap >= 50:
            base -= 20
    return clamp_score(base)


def category_for_score(score: int) -> str:
    if score >= 85:
        return "True"
    if score >= 70:
        return "Mostly True"
    if score >= 45:
        return "Misleading or Mixed"
    if score >= 25:
        return "Mostly False"
    return "False"


def clamp_score(value: int) -> int:
    return max(0, min(100, int(value)))


def combine_reasoning_summaries(verifiers: list[VerifierOutput]) -> str:
    summaries = [item.reasoning_summary.strip() for item in verifiers if item.reasoning_summary.strip()]
    if not summaries:
        return "The final result is based on the supplied evidence and verifier scores."
    if len(summaries) == 1:
        return summaries[0]
    return " ".join(summaries[:2])


def apply_source_credibility_adjustment(
    result: ConsensusResult,
    source_credibility: SourceCredibilityAssessment | None,
) -> ConsensusResult:
    if source_credibility is None:
        return result

    score = source_credibility.source_trust_score
    risk = source_credibility.website_risk_level
    confidence = result.confidence_score
    explanation = result.concise_explanation

    if risk == "High":
        confidence -= 20
    elif risk == "Medium":
        confidence -= 8

    if source_credibility.duplicate_or_syndication_risk == "High":
        confidence -= 8

    if score < 40 or (
        risk == "High"
        and source_credibility.independent_source_count <= 1
        and source_credibility.high_quality_source_count == 0
    ):
        return ConsensusResult(
            final_verdict="Unverified",
            truth_score=50,
            confidence_score=clamp_score(min(confidence, 35)),
            concise_explanation=(
                f"{explanation} Source credibility was too weak for a firm verdict: "
                f"{source_credibility.summary}"
            ),
            used_judge=result.used_judge,
        )

    return ConsensusResult(
        final_verdict=result.final_verdict,
        truth_score=result.truth_score,
        confidence_score=clamp_score(confidence),
        concise_explanation=(
            f"{explanation} Source credibility adjustment: {source_credibility.summary}"
        ),
        used_judge=result.used_judge,
    )
