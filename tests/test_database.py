from __future__ import annotations

from backend.database import AuditStore
from backend.schemas.models import FactCheckReport, GonkaTraceRecord


def test_audit_store_persists_run_events_report_and_gonka_ids(tmp_path):
    store = AuditStore(tmp_path / "verity-test.db")
    run_id = store.create_run(
        input_type="text",
        input_text="The city opened a rail line.",
        article_url="",
        image_name="",
        mode="professional",
    )
    store.append_event(
        run_id,
        sequence_number=1,
        stage="Input received",
        timestamp_utc="2026-09-04T00:00:00Z",
        details={"has_text": True},
    )
    report = FactCheckReport(
        extracted_claim="The city opened a rail line.",
        extracted_claims=["The city opened a rail line."],
        final_verdict="True",
        truth_score=90,
        confidence_score=82,
        concise_explanation="Two sources and two models support the claim.",
        gonka_trace=[
            GonkaTraceRecord(
                step_name="verifier_1",
                requested_model_id="provider/model-a",
                returned_model_id="provider/model-a",
                response_body_id="chatcmpl-test",
                request_id="req-test",
                trace_id="trace-test",
                timestamp_utc="2026-09-04T00:00:01Z",
                latency_ms=125.5,
                token_usage={"total_tokens": 42},
                success=True,
            )
        ],
    )
    store.complete_run(run_id, report, "2026-09-04T00:00:02Z")

    summaries = store.list_runs()
    record = store.get_run(run_id)

    assert summaries[0]["id"] == run_id
    assert summaries[0]["gonka_call_count"] == 1
    assert record is not None
    assert record["status"] == "completed"
    assert record["events"][0]["details"] == {"has_text": True}
    assert record["report"]["final_verdict"] == "True"
    assert record["gonka_calls"][0]["request_id"] == "req-test"
    assert record["gonka_calls"][0]["trace_id"] == "trace-test"
    assert record["gonka_calls"][0]["token_usage"] == {"total_tokens": 42}


def test_audit_store_records_safe_failure(tmp_path):
    store = AuditStore(tmp_path / "verity-test.db")
    run_id = store.create_run(
        input_type="url",
        input_text="",
        article_url="https://example.com/article",
        image_name="",
        mode="quick",
    )
    store.fail_run(run_id, "Safe failure", "2026-09-04T00:00:02Z")

    record = store.get_run(run_id)

    assert record is not None
    assert record["status"] == "failed"
    assert record["error_message"] == "Safe failure"
    assert record["report"] is None
