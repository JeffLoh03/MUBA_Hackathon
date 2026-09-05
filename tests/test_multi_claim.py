from __future__ import annotations

import itertools
import json
from types import SimpleNamespace

import pytest

from backend.config import AppConfig
from backend.pipeline.image_pipeline import ImageFactCheckPipeline
from backend.pipeline.text_pipeline import TextFactCheckPipeline
from backend.schemas.models import EvidenceItem, FactCheckReport, GonkaTraceRecord, SearchQueries, SearchResult
from backend.services.gonka_client import GonkaCallFailed, GonkaClientError
from backend.services.image_processor import ProcessedImage


CLAIM_TRUE = "Earth has one natural moon."
CLAIM_FALSE = "Mars has seven natural moons."


class ClaimAwareClient:
    def __init__(self, claims: list[str], *, failed_claim: str = ""):
        self.claims = claims
        self.failed_claim = failed_claim
        self.calls: list[tuple[str, dict]] = []
        self.sequence = itertools.count(1)

    def chat_json(self, *, step_name, model_id, prompt, user_payload):
        self.calls.append((step_name, user_payload))
        call_id = next(self.sequence)
        claim = user_payload.get("claim", "")
        failed = claim == self.failed_claim and step_name.startswith("verifier")
        trace = GonkaTraceRecord(
            step_name=step_name,
            requested_model_id=model_id,
            request_id=f"req-{call_id}",
            timestamp_utc="2026-09-05T00:00:00Z",
            latency_ms=1,
            success=not failed,
        )
        if failed:
            raise GonkaCallFailed(GonkaClientError("Provider unavailable"), trace)
        if step_name == "claim_extraction":
            data = {"claims": self.claims}
        elif step_name == "search_planning":
            data = {field: claim for field in SearchQueries.model_fields}
        else:
            assert step_name.startswith("verifier")
            assert all(claim.rstrip(".") in item["excerpt"] for item in user_payload["evidence"])
            is_true = claim != CLAIM_FALSE
            data = {
                "verdict": "true" if is_true else "false",
                "support_score": 94 if is_true else 6,
                "confidence": 90,
                "supporting_evidence": ["E1"] if is_true else [],
                "contradicting_evidence": [] if is_true else ["E1"],
                "reasoning_summary": f"Evidence independently reviewed for: {claim}",
            }
        return SimpleNamespace(text=json.dumps(data), trace=trace)


class ClaimAwareSearch:
    def __init__(self, *, failed_claim: str = ""):
        self.failed_claim = failed_claim
        self.calls: list[list[str]] = []

    def search_many(self, queries, max_results_per_query):
        self.calls.append(queries)
        # Quick mode's final query retains the quoted original claim.
        claim = queries[0] if queries[0].endswith(".") else queries[-1].split('"')[1]
        if claim == self.failed_claim:
            raise RuntimeError("Do not expose raw provider credentials in a failure report")
        return [SearchResult(url="https://example.gov/evidence", raw_content=claim)]


class ClaimAwareEvidence:
    def build_evidence(self, search_results):
        return [
            EvidenceItem(
                evidence_id="E1",
                title="Independent factual record",
                url="https://example.gov/evidence",
                root_domain="example.gov",
                excerpt=search_results[0].raw_content,
                published_date="2026-09-05",
                retrieved_at="2026-09-05T00:00:00Z",
                source_type="official",
                source_quality=0.95,
            )
        ]


def make_pipeline(claims, *, quick=False, failed_search="", failed_verifiers="", events=None):
    config = AppConfig(
        gonka_base_url="https://api.gonkarouter.io/v1",
        gonka_api_key="fake-test-key",
        gonka_claim_model="claim-model",
        gonka_verify_model_1="verifier-a",
        gonka_verify_model_2="verifier-b",
        gonka_judge_model="judge-model",
        gonka_vision_model="",
        search_provider="duckduckgo",
        tavily_api_key="",
        env_file_found=False,
    )
    client = ClaimAwareClient(claims, failed_claim=failed_verifiers)
    search = ClaimAwareSearch(failed_claim=failed_search)
    pipeline = TextFactCheckPipeline(
        config,
        client,
        search,
        ClaimAwareEvidence(),
        use_ai_claim_extraction=not quick,
        use_ai_search_planning=not quick,
        progress_callback=(lambda stage, details: events.append((stage, details))) if events is not None else None,
    )
    return pipeline, client, search


def test_distinct_claims_get_independent_verdicts_and_one_scoped_ledger():
    events = []
    pipeline, client, search = make_pipeline([CLAIM_TRUE, CLAIM_FALSE], events=events)

    report = pipeline.verify(text=f"{CLAIM_TRUE}\n{CLAIM_FALSE}")

    assert report.final_verdict == "Multiple claims reviewed"
    assert report.review_status == "completed"
    assert [child.final_verdict for child in report.claim_reports] == ["True", "False"]
    assert [child.truth_score for child in report.claim_reports] == [94, 6]
    assert [child.extracted_claims for child in report.claim_reports] == [[CLAIM_TRUE], [CLAIM_FALSE]]
    assert report.all_evidence == []  # E1 from different claims is never mixed in a shared bucket.
    assert all(not child.claim_reports and not child.gonka_trace for child in report.claim_reports)
    assert report.claim_reports[0].supporting_evidence[0].excerpt == CLAIM_TRUE
    assert report.claim_reports[1].contradicting_evidence[0].excerpt == CLAIM_FALSE
    assert len(search.calls) == 2
    assert len([step for step, _ in client.calls if step == "claim_extraction"]) == 1
    assert len(report.gonka_trace) == len(client.calls) == 7
    assert len({trace.request_id for trace in report.gonka_trace}) == 7
    assert report.gonka_trace[0].claim_index is None
    for index, claim in enumerate([CLAIM_TRUE, CLAIM_FALSE], 1):
        claim_traces = [trace for trace in report.gonka_trace if trace.claim_index == index]
        assert len(claim_traces) == 3
        assert all(trace.claim == claim for trace in claim_traces)
    calls = [details for stage, details in events if stage == "Gonka call completed"]
    assert [details["claim_index"] for details in calls if "claim_index" in details].count(2) == 3
    assert FactCheckReport.model_validate_json(report.model_dump_json()) == report


@pytest.mark.parametrize(
    "text",
    [f"{CLAIM_TRUE}\n{CLAIM_FALSE}", f"{CLAIM_TRUE} {CLAIM_FALSE}", f"{CLAIM_TRUE}\n{CLAIM_FALSE}\n" + "Background. " * 50],
)
def test_quick_mode_extracts_multiple_statements_once(text):
    pipeline, client, _ = make_pipeline([CLAIM_TRUE, CLAIM_FALSE], quick=True)

    report = pipeline.verify(text=text)

    assert len(report.claim_reports) == 2
    assert sum(step == "claim_extraction" for step, _ in client.calls) == 1
    assert not any(step == "search_planning" for step, _ in client.calls)
    assert [child.final_verdict for child in report.claim_reports] == ["True", "False"]


def test_duplicate_and_blank_claims_are_removed_before_three_claim_cap():
    third = "Jupiter has rings."
    fourth = "Saturn has rings."
    pipeline, client, _ = make_pipeline(
        [" ", CLAIM_TRUE, CLAIM_TRUE.upper().rstrip("."), CLAIM_FALSE, third, fourth]
    )

    report = pipeline.verify(text="An article containing four distinct astronomy statements.")

    assert report.extracted_claims == [CLAIM_TRUE, CLAIM_FALSE, third]
    assert len(report.claim_reports) == 3
    assert report.unreviewed_claims == [fourth]
    assert report.review_status == "partial"
    assert not any(payload.get("claim") == fourth for _, payload in client.calls)
    assert any("not reviewed" in limitation for limitation in report.limitations)


def test_one_failed_claim_does_not_discard_other_claim_or_completed_request_ids():
    pipeline, client, _ = make_pipeline([CLAIM_TRUE, CLAIM_FALSE], failed_search=CLAIM_TRUE)

    report = pipeline.verify(text="An article containing two distinct statements.")

    assert report.review_status == "partial"
    first, second = report.claim_reports
    assert first.review_status == "failed"
    assert first.final_verdict == "Unverified"
    assert first.confidence_score == 0
    assert second.final_verdict == "False"
    assert second.review_status == "completed"
    assert "credentials" not in report.model_dump_json()
    assert len(report.gonka_trace) == len(client.calls)
    assert any(trace.step_name == "search_planning" and trace.claim_index == 1 for trace in report.gonka_trace)


def test_all_verifiers_failing_for_one_claim_is_reported_without_poisoning_next_claim():
    pipeline, client, _ = make_pipeline([CLAIM_TRUE, CLAIM_FALSE], failed_verifiers=CLAIM_TRUE)

    report = pipeline.verify(text="An article containing two distinct statements.")

    assert report.review_status == "partial"
    assert report.claim_reports[0].final_verdict == "Unverified"
    assert report.claim_reports[0].review_status == "failed"
    assert report.claim_reports[1].final_verdict == "False"
    assert len(report.gonka_trace) == len(client.calls)
    failures = [trace for trace in report.gonka_trace if not trace.success]
    assert len(failures) == 4
    assert all(trace.claim_index == 1 for trace in failures)


def test_single_claim_retains_existing_score_and_trace_shape():
    pipeline, client, _ = make_pipeline([CLAIM_TRUE, " " + CLAIM_TRUE.upper()])

    report = pipeline.verify(text=CLAIM_TRUE)

    assert report.claim_reports == []
    assert report.extracted_claim == CLAIM_TRUE
    assert report.extracted_claims == [CLAIM_TRUE]
    assert report.final_verdict == "True"
    assert len(report.gonka_trace) == len(client.calls) == 4
    assert all(trace.claim_index is None for trace in report.gonka_trace)


def test_multi_claim_image_keeps_separate_context_assessments(monkeypatch):
    monkeypatch.setattr(
        "backend.pipeline.image_pipeline.process_image",
        lambda data, mime: ProcessedImage(data, mime, CLAIM_FALSE, {}, 100, 100),
    )
    pipeline, client, _ = make_pipeline([CLAIM_TRUE, CLAIM_FALSE])
    image_pipeline = ImageFactCheckPipeline(pipeline, client)

    report = image_pipeline.verify(image_bytes=b"stub", mime_type="image/png", caption_or_claim=CLAIM_TRUE)

    assert report.image_context_assessment.verdict == "Multiple claims reviewed"
    assert [child.image_context_assessment.verdict for child in report.claim_reports] == [
        "Context Supported", "Misleading Caption",
    ]
    assert report.image_context_assessment.ocr_text == CLAIM_FALSE
    assert all(child.image_context_assessment.ocr_text == CLAIM_FALSE for child in report.claim_reports)
    assert len(report.gonka_trace) == len(client.calls)
