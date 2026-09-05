import pytest

from backend.pipeline.text_pipeline import (
    validate_english_claim_extraction,
    validate_english_evidence_gap_plan,
    validate_verifier_output,
)


def test_non_english_model_explanation_is_rejected_for_retry():
    with pytest.raises(ValueError, match="must be written in English"):
        validate_verifier_output({
            "verdict": "true", "support_score": 90, "confidence": 85,
            "supporting_evidence": ["E1"], "contradicting_evidence": [],
            "context_mismatch": False,
            "reasoning_summary": "证据支持这项说法。", "missing_information": [],
        }, {"E1"})


def test_claims_and_professional_explanations_require_english():
    with pytest.raises(ValueError, match="claim extraction"):
        validate_english_claim_extraction({"claims": ["这是一个事实声明。"], "not_verifiable_reason": ""})
    with pytest.raises(ValueError, match="professional research explanation"):
        validate_english_evidence_gap_plan({
            "summary": "需要更多资料。", "gaps": [], "follow_up_queries": []
        })


def test_english_generated_fields_are_accepted():
    claim = validate_english_claim_extraction({
        "claims": ["The agency published the report in 2025."], "not_verifiable_reason": ""
    })
    verifier = validate_verifier_output({
        "verdict": "true", "support_score": 90, "confidence": 85,
        "supporting_evidence": ["E1"], "contradicting_evidence": [],
        "context_mismatch": False, "reasoning_summary": "The official report supports the claim.",
        "missing_information": [],
    }, {"E1"})
    assert claim.claims and verifier.reasoning_summary
