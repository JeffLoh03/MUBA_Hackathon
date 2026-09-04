from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

import api
from schemas.models import FactCheckReport


def test_health_endpoint():
    response = TestClient(api.app).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_verification_stream_uses_pipeline_without_network(monkeypatch):
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

    response = TestClient(api.app).post(
        "/api/verify/stream",
        json={"url": "https://example.com/news", "mode": "quick", "show_browser": False},
    )
    messages = [json.loads(line) for line in response.text.splitlines() if line]

    assert response.status_code == 200
    assert [message["type"] for message in messages] == ["progress", "report"]
    assert messages[0]["data"]["details"]["evidence_count"] == 2
    assert messages[1]["data"]["report"]["final_verdict"] == "Unverified"
    assert received == {
        "article_url": "https://example.com/news",
        "text": "",
        "use_ai_search_planning": False,
        "use_ai_claim_extraction": False,
    }


def test_text_claim_request_is_forwarded_without_network(monkeypatch):
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

    response = TestClient(api.app).post(
        "/api/verify/stream",
        json={"text": "The moon is made of cheese.", "mode": "quick"},
    )

    assert response.status_code == 200
    assert received == {"article_url": "", "text": "The moon is made of cheese."}


def test_image_request_is_forwarded_without_network(monkeypatch):
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

    response = TestClient(api.app).post(
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
