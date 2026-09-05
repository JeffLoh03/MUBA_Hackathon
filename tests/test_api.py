from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend import api
from backend.auth import Credentials, SESSION_COOKIE
from backend.database import AuditStore
from backend.schemas.models import FactCheckReport


@pytest.fixture(autouse=True)
def isolated_audit_store(monkeypatch, tmp_path):
    store = AuditStore(tmp_path / "audit-test.db")
    monkeypatch.setattr(api, "audit_store", store)
    return store


@pytest.fixture
def authenticated_client(isolated_audit_store):
    client = TestClient(api.app)
    store = api.auth_store()
    user = store.create_first_user(Credentials(email="owner@example.com", password="test-password-long-enough"))
    client.cookies.set(SESSION_COOKIE, store.create_session(user))
    return client


def test_health_endpoint():
    response = TestClient(api.app).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_verification_stream_uses_pipeline_without_network(monkeypatch, isolated_audit_store, authenticated_client):
    received = {}

    class FakePipeline:
        def __init__(self, **kwargs):
            self.progress_callback = kwargs["progress_callback"]
            received["use_ai_search_planning"] = kwargs["use_ai_search_planning"]
            received["use_ai_claim_extraction"] = kwargs["use_ai_claim_extraction"]

        def verify(self, *, article_url="", text=""):
            received.update({"article_url": article_url, "text": text})
            self.progress_callback("Evidence processing completed", {"evidence_count": 2})
            return FactCheckReport(
                extracted_claim="A test claim",
                extracted_claims=["A test claim"],
                final_verdict="Unverified",
                truth_score=50,
                confidence_score=25,
                concise_explanation="Offline fake report.",
            )

    monkeypatch.setattr(api, "load_config", lambda path: object())
    monkeypatch.setattr(api, "GonkaClient", lambda config: object())
    monkeypatch.setattr(api, "TextFactCheckPipeline", FakePipeline)

    response = authenticated_client.post(
        "/api/verify/stream",
        json={"url": "https://example.com/news", "mode": "quick", "show_browser": False},
    )
    messages = [json.loads(line) for line in response.text.splitlines() if line]

    assert response.status_code == 200
    assert [message["type"] for message in messages] == ["run", "progress", "report"]
    assert messages[0]["data"]["run_id"] == response.headers["x-verification-id"]
    assert messages[1]["data"]["details"]["evidence_count"] == 2
    assert messages[2]["data"]["report"]["final_verdict"] == "Unverified"
    assert received == {
        "article_url": "https://example.com/news",
        "text": "",
        "use_ai_search_planning": False,
        "use_ai_claim_extraction": False,
    }
    run_id = messages[0]["data"]["run_id"]
    stored = isolated_audit_store.get_run(run_id)
    assert stored is not None
    assert stored["status"] == "completed"
    assert stored["events"][0]["stage"] == "Evidence processing completed"

    audit_response = authenticated_client.get(f"/api/audits/{run_id}")
    assert audit_response.status_code == 200
    assert audit_response.json()["report"]["final_verdict"] == "Unverified"


def test_text_claim_request_is_forwarded_without_network(monkeypatch, authenticated_client):
    received = {}

    class FakePipeline:
        def __init__(self, **kwargs):
            pass

        def verify(self, *, article_url="", text=""):
            received.update({"article_url": article_url, "text": text})
            return FactCheckReport(
                extracted_claim=text,
                extracted_claims=[text],
                final_verdict="Unverified",
                truth_score=50,
                confidence_score=25,
                concise_explanation="Offline fake report.",
            )

    monkeypatch.setattr(api, "load_config", lambda path: object())
    monkeypatch.setattr(api, "GonkaClient", lambda config: object())
    monkeypatch.setattr(api, "TextFactCheckPipeline", FakePipeline)

    response = authenticated_client.post(
        "/api/verify/stream",
        json={"text": "The moon is made of cheese.", "mode": "quick"},
    )

    assert response.status_code == 200
    assert received == {"article_url": "", "text": "The moon is made of cheese."}


def test_image_request_is_forwarded_without_network(monkeypatch, authenticated_client):
    received = {}

    class FakeTextPipeline:
        def __init__(self, **kwargs):
            received["use_ai_search_planning"] = kwargs["use_ai_search_planning"]
            received["use_ai_claim_extraction"] = kwargs["use_ai_claim_extraction"]

    class FakeImagePipeline:
        def __init__(self, **kwargs):
            received["vision_model_id"] = kwargs["vision_model_id"]

        def verify(self, *, image_bytes, mime_type, caption_or_claim=""):
            received.update(
                {
                    "image_bytes": image_bytes,
                    "mime_type": mime_type,
                    "caption": caption_or_claim,
                }
            )
            return FactCheckReport(
                extracted_claim=caption_or_claim,
                extracted_claims=[caption_or_claim],
                final_verdict="Unverified",
                truth_score=50,
                confidence_score=25,
                concise_explanation="Offline image report.",
            )

    config = SimpleNamespace(gonka_vision_model="")
    monkeypatch.setattr(api, "load_config", lambda path: config)
    monkeypatch.setattr(api, "GonkaClient", lambda loaded_config: object())
    monkeypatch.setattr(api, "TextFactCheckPipeline", FakeTextPipeline)
    monkeypatch.setattr(api, "ImageFactCheckPipeline", FakeImagePipeline)

    response = authenticated_client.post(
        "/api/verify/image/stream",
        data={"caption": "This photo shows a flood today.", "mode": "professional"},
        files={"image": ("claim.png", b"fake-image-bytes", "image/png")},
    )

    assert response.status_code == 200
    assert received["image_bytes"] == b"fake-image-bytes"
    assert received["mime_type"] == "image/png"
    assert received["caption"] == "This photo shows a flood today."
    assert received["use_ai_search_planning"] is True
    assert received["use_ai_claim_extraction"] is True


def test_api_error_redaction(monkeypatch):
    secret = "gonka-super-secret"
    monkeypatch.setenv("GONKA_API_KEY", secret)

    message = api.safe_error_message(RuntimeError(f"Authorization: Bearer {secret}"))

    assert secret not in message
    assert "[REDACTED]" in message


def test_verification_returns_busy_when_capacity_is_exhausted(monkeypatch, authenticated_client):
    class BusySemaphore:
        def acquire(self, *, blocking):
            assert blocking is False
            return False

    monkeypatch.setattr(api, "verification_slots", BusySemaphore())

    response = authenticated_client.post(
        "/api/verify/stream",
        json={"text": "A test claim"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "The verification service is busy. Try again shortly."


def test_completed_verification_is_available_in_audit_api(authenticated_client):
    response = authenticated_client.get("/api/audits")

    assert response.status_code == 200
    assert response.json() == {"runs": []}


def test_unknown_audit_returns_not_found(authenticated_client):
    response = authenticated_client.get(f"/api/audits/{'0' * 32}")

    assert response.status_code == 404
