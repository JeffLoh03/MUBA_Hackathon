from __future__ import annotations

import io
import json
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from PIL import Image

from config import AppConfig, read_timeout_seconds
from pipeline.consensus import build_consensus, needs_judge
from pipeline.image_pipeline import ImageFactCheckPipeline
from pipeline.text_pipeline import TextFactCheckPipeline, build_verifier_payload, validate_verifier_output
from schemas.models import ArticleContent, EvidenceItem, FactCheckReport, GonkaTraceRecord, SearchResult, VerifierOutput
from services.article_extractor import URLSafetyError, validate_public_url
from services.evidence_processor import (
    EvidenceProcessor,
    dedupe_search_results,
    distinctive_claim_anchors,
    remove_near_duplicates,
)
from services.gonka_client import (
    GonkaCallFailed,
    GonkaClient,
    GonkaClientError,
    extract_request_trace_ids,
    parse_json_object,
    redact_secrets,
    strip_private_reasoning,
)
from services.image_processor import process_image
from services.search_provider import (
    SearchProvider,
    SearchProviderError,
    bing_search,
    ddgs_search,
    deterministic_search_queries,
    duckduckgo_search,
)
from services.source_credibility import assess_source_credibility
from services.source_ranker import classify_source, publisher_from_url, root_domain


def make_config() -> AppConfig:
    return AppConfig(
        gonka_base_url="https://api.gonkarouter.io/v1",
        gonka_api_key="test-gonka-key",
        gonka_claim_model="model-claim",
        gonka_verify_model_1="model-a",
        gonka_verify_model_2="model-b",
        gonka_judge_model="model-judge",
        gonka_vision_model="model-vision",
        search_provider="duckduckgo",
        tavily_api_key="",
        env_file_found=True,
    )


def test_default_gonka_deadline_allows_slow_reasoning_models(monkeypatch):
    monkeypatch.delenv("GONKA_TIMEOUT_SECONDS", raising=False)

    assert read_timeout_seconds() == 90.0


def make_trace(step: str, model: str = "model-a", success: bool = True) -> GonkaTraceRecord:
    return GonkaTraceRecord(
        step_name=step,
        requested_model_id=model,
        returned_model_id=model if success else None,
        response_body_id="chatcmpl-test" if success else None,
        request_id="req-test",
        trace_id="trace-test",
        timestamp_utc="2026-08-29T00:00:00Z",
        latency_ms=12.3,
        token_usage={"total_tokens": 42} if success else None,
        success=success,
        error_type=None if success else "FakeError",
        safe_error_message=None if success else "safe failure",
    )


class FakeTextResult:
    def __init__(self, text: str, step_name: str, model_id: str = "model-a") -> None:
        self.text = text
        self.trace = make_trace(step_name, model_id)


class FakeGonkaClient:
    def __init__(self, responses: dict[str, list[str]] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, str]] = []
        self.max_tokens_by_step: dict[str, int] = {}

    def chat_json(self, *, step_name, model_id, prompt, user_payload, max_tokens=1024):
        base_step = step_name.replace("_retry", "").replace("_recovery", "")
        self.calls.append((step_name, model_id))
        self.max_tokens_by_step[step_name] = max_tokens
        values = self.responses.get(base_step)
        if not values:
            raise AssertionError(f"No fake response configured for {base_step}")
        text = values.pop(0)
        return FakeTextResult(text, step_name, model_id)

    def describe_image(self, *, model_id, image_bytes, mime_type, caption, ocr_text):
        raise AssertionError("Vision should not be called in this test")


class FailingVisionGonkaClient(FakeGonkaClient):
    def describe_image(self, *, model_id, image_bytes, mime_type, caption, ocr_text):
        error = GonkaClientError("vision unavailable", error_type="UnsupportedVision")
        raise GonkaCallFailed(error, make_trace("image_context_analysis", model_id, success=False))


class TimeoutClaimGonkaClient(FakeGonkaClient):
    def chat_json(self, *, step_name, model_id, prompt, user_payload, max_tokens=1024):
        if step_name.startswith("claim_extraction"):
            self.calls.append((step_name, model_id))
            error = GonkaClientError(
                "Timeout while contacting Gonka Router.",
                error_type="APITimeoutError",
            )
            trace = make_trace(step_name, model_id, success=False).model_copy(
                update={
                    "error_type": "APITimeoutError",
                    "safe_error_message": "Timeout while contacting Gonka Router.",
                }
            )
            raise GonkaCallFailed(error, trace)
        return super().chat_json(
            step_name=step_name,
            model_id=model_id,
            prompt=prompt,
            user_payload=user_payload,
            max_tokens=max_tokens,
        )


class FailingFirstVerifierGonkaClient(FakeGonkaClient):
    def chat_json(self, *, step_name, model_id, prompt, user_payload, max_tokens=1024):
        if step_name.startswith("verifier_1"):
            self.calls.append((step_name, model_id))
            error = GonkaClientError("Verifier timed out.", error_type="APITimeoutError")
            trace = make_trace(step_name, model_id, success=False).model_copy(
                update={
                    "error_type": "APITimeoutError",
                    "safe_error_message": "Verifier timed out.",
                }
            )
            raise GonkaCallFailed(error, trace)
        return super().chat_json(
            step_name=step_name,
            model_id=model_id,
            prompt=prompt,
            user_payload=user_payload,
            max_tokens=max_tokens,
        )


class TimeoutSecondVerifierGonkaClient(FakeGonkaClient):
    def chat_json(self, *, step_name, model_id, prompt, user_payload, max_tokens=1024):
        if step_name.startswith("verifier_2"):
            self.calls.append((step_name, model_id))
            error = GonkaClientError("Verifier timed out.", error_type="APITimeoutError")
            trace = make_trace(step_name, model_id, success=False).model_copy(
                update={
                    "error_type": "APITimeoutError",
                    "safe_error_message": "Verifier timed out.",
                }
            )
            raise GonkaCallFailed(error, trace)
        return super().chat_json(
            step_name=step_name,
            model_id=model_id,
            prompt=prompt,
            user_payload=user_payload,
            max_tokens=max_tokens,
        )


class RecoveringJudgeModelGonkaClient(FakeGonkaClient):
    def chat_json(self, *, step_name, model_id, prompt, user_payload, max_tokens=1024):
        if step_name == "verifier_2":
            self.calls.append((step_name, model_id))
            error = GonkaClientError("Rate limited.", error_type="RateLimitError")
            trace = make_trace(step_name, model_id, success=False).model_copy(
                update={
                    "error_type": "RateLimitError",
                    "safe_error_message": "Rate limited.",
                }
            )
            raise GonkaCallFailed(error, trace)
        return super().chat_json(
            step_name=step_name,
            model_id=model_id,
            prompt=prompt,
            user_payload=user_payload,
            max_tokens=max_tokens,
        )


class FailingPrimaryVerifiersGonkaClient(FakeGonkaClient):
    def chat_json(self, *, step_name, model_id, prompt, user_payload, max_tokens=1024):
        if step_name.startswith(("verifier_1", "verifier_2")):
            self.calls.append((step_name, model_id))
            error = GonkaClientError("Primary verifier unavailable.", error_type="APITimeoutError")
            trace = make_trace(step_name, model_id, success=False).model_copy(
                update={
                    "error_type": "APITimeoutError",
                    "safe_error_message": "Primary verifier unavailable.",
                }
            )
            raise GonkaCallFailed(error, trace)
        return super().chat_json(
            step_name=step_name,
            model_id=model_id,
            prompt=prompt,
            user_payload=user_payload,
            max_tokens=max_tokens,
        )


class RecoveringQuorumGonkaClient(FakeGonkaClient):
    def chat_json(self, *, step_name, model_id, prompt, user_payload, max_tokens=1024):
        if step_name in {"verifier_1", "verifier_2"}:
            self.calls.append((step_name, model_id))
            error = GonkaClientError("Temporary verifier failure.", error_type="RateLimitError")
            trace = make_trace(step_name, model_id, success=False).model_copy(
                update={
                    "error_type": "RateLimitError",
                    "safe_error_message": "Temporary verifier failure.",
                }
            )
            raise GonkaCallFailed(error, trace)
        return super().chat_json(
            step_name=step_name,
            model_id=model_id,
            prompt=prompt,
            user_payload=user_payload,
            max_tokens=max_tokens,
        )


class CoordinatedVerifierGonkaClient(FakeGonkaClient):
    def __init__(self, responses):
        super().__init__(responses)
        self.verifier_barrier = threading.Barrier(2)

    def chat_json(self, *, step_name, model_id, prompt, user_payload, max_tokens=1024):
        if step_name in {"verifier_1", "verifier_2"}:
            self.verifier_barrier.wait(timeout=1)
        return super().chat_json(
            step_name=step_name,
            model_id=model_id,
            prompt=prompt,
            user_payload=user_payload,
            max_tokens=max_tokens,
        )


class CoordinatedThreeVerifierGonkaClient(FakeGonkaClient):
    def __init__(self, responses):
        super().__init__(responses)
        self.verifier_barrier = threading.Barrier(3)

    def chat_json(self, *, step_name, model_id, prompt, user_payload, max_tokens=1024):
        if step_name in {"verifier_1", "verifier_2", "verifier_fallback"}:
            self.verifier_barrier.wait(timeout=1)
        return super().chat_json(
            step_name=step_name,
            model_id=model_id,
            prompt=prompt,
            user_payload=user_payload,
            max_tokens=max_tokens,
        )


class FakeSearchProvider:
    def search_many(self, queries, max_results_per_query=4):
        return [SearchResult(title="Evidence", url="https://example.com/evidence", snippet="Evidence text")]


class RecordingSearchProvider(FakeSearchProvider):
    def __init__(self):
        self.queries = []

    def search_many(self, queries, max_results_per_query=4):
        self.queries = list(queries)
        return super().search_many(queries, max_results_per_query=max_results_per_query)


class FailedSearchProvider:
    last_errors = ["DuckDuckGo search failed: HTTP 503"]

    def search_many(self, queries, max_results_per_query=4):
        return []


class FakeEvidenceProcessor:
    def __init__(self, evidence: list[EvidenceItem]) -> None:
        self.evidence = evidence

    def build_evidence(self, search_results, *, claim=""):
        return self.evidence


class FakeVisibleBrowser:
    def __init__(self) -> None:
        self.searches: list[str] = []
        self.urls: list[str] = []

    def show_search(self, query: str) -> None:
        self.searches.append(query)

    def show_url(self, url: str) -> None:
        self.urls.append(url)


class FakeTextPipeline:
    def verify(self, *, text="", article_url=""):
        return FactCheckReport(
            extracted_claim="This image shows a 2026 flood in Kuala Lumpur.",
            extracted_claims=["This image shows a 2026 flood in Kuala Lumpur."],
            final_verdict="Unverified",
            truth_score=50,
            confidence_score=35,
            concise_explanation="Evidence was insufficient.",
            all_evidence=[],
            gonka_trace=[make_trace("verifier_1")],
            limitations=[],
        )


def evidence_item(
    evidence_id="E1",
    url="https://reuters.com/world/test",
    title="Reliable evidence",
    quality=0.78,
    excerpt="A reliable article says the claim is supported.",
) -> EvidenceItem:
    source_type, default_quality = classify_source(url)
    return EvidenceItem(
        evidence_id=evidence_id,
        title=title,
        url=url,
        root_domain=root_domain(url),
        publisher=publisher_from_url(url),
        published_date="2026-08-01",
        retrieved_at="2026-08-29T00:00:00Z",
        excerpt=excerpt,
        source_type=source_type,
        source_quality=quality if quality is not None else default_quality,
    )


def verifier(
    verdict="true",
    support_score=90,
    confidence=80,
    support=None,
    contradict=None,
    context_mismatch=False,
) -> VerifierOutput:
    return VerifierOutput(
        verdict=verdict,
        support_score=support_score,
        confidence=confidence,
        supporting_evidence=support or ["E1"],
        contradicting_evidence=contradict or [],
        context_mismatch=context_mismatch,
        reasoning_summary="Verifier summary.",
        missing_information=[],
    )


def make_png_bytes() -> bytes:
    image = Image.new("RGB", (200, 80), color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_text_claim_extraction_and_report():
    gonka = FakeGonkaClient(
        {
            "claim_extraction": ['{"claims":["The city opened a new rail line in 2026"],"not_verifiable_reason":""}'],
            "search_planning": [
                '{"general_query":"rail line 2026","official_source_query":"rail line 2026 official","supporting_evidence_query":"rail line 2026 evidence","contradicting_evidence_query":"rail line 2026 false","date_context_query":"rail line 2026 date","old_news_or_misinformation_query":"rail line 2026 old news"}'
            ],
            "verifier_1": [
                '{"verdict":"true","support_score":90,"confidence":80,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Evidence supports the claim.","missing_information":[]}'
            ],
            "verifier_2": [
                '{"verdict":"true","support_score":88,"confidence":75,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Independent review supports it.","missing_information":[]}'
            ],
        }
    )
    pipeline = TextFactCheckPipeline(
        make_config(),
        gonka,
        FakeSearchProvider(),
        FakeEvidenceProcessor([evidence_item()]),
    )

    report = pipeline.verify(text="The city opened a new rail line in 2026.")

    assert report.extracted_claim == "The city opened a new rail line in 2026"
    assert report.final_verdict == "True"
    assert report.supporting_evidence[0].evidence_id == "E1"
    assert len(report.gonka_trace) == 4


def test_text_pipeline_emits_live_progress_events():
    events = []
    gonka = FakeGonkaClient(
        {
            "claim_extraction": ['{"claims":["The city opened a new rail line in 2026"],"not_verifiable_reason":""}'],
            "search_planning": [
                '{"general_query":"rail line 2026","official_source_query":"rail line 2026 official","supporting_evidence_query":"rail line 2026 evidence","contradicting_evidence_query":"rail line 2026 false","date_context_query":"rail line 2026 date","old_news_or_misinformation_query":"rail line 2026 old news"}'
            ],
            "verifier_1": [
                '{"verdict":"true","support_score":90,"confidence":80,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Evidence supports the claim.","missing_information":[]}'
            ],
            "verifier_2": [
                '{"verdict":"true","support_score":88,"confidence":75,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Independent review supports it.","missing_information":[]}'
            ],
        }
    )
    pipeline = TextFactCheckPipeline(
        make_config(),
        gonka,
        FakeSearchProvider(),
        FakeEvidenceProcessor([evidence_item()]),
        progress_callback=lambda stage, details: events.append((stage, details)),
    )

    pipeline.verify(text="The city opened a new rail line in 2026.")

    stages = [stage for stage, _ in events]
    assert "Claim extraction started" in stages
    assert "Web search completed" in stages
    assert "Source credibility scored" in stages
    assert "Verifier 1 completed" in stages
    assert "Consensus completed" in stages


def test_quick_pipeline_uses_deterministic_search_plan_without_gonka_call():
    gonka = FakeGonkaClient(
        {
            "verifier_1": [
                '{"verdict":"true","support_score":91,"confidence":82,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Supported.","missing_information":[]}'
            ],
            "verifier_2": [
                '{"verdict":"true","support_score":89,"confidence":80,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Supported.","missing_information":[]}'
            ],
        }
    )
    search_provider = RecordingSearchProvider()
    pipeline = TextFactCheckPipeline(
        make_config(),
        gonka,
        search_provider,
        FakeEvidenceProcessor([evidence_item()]),
        use_ai_search_planning=False,
        use_ai_claim_extraction=False,
    )

    report = pipeline.verify(text="NASA confirms DART changed an asteroid's orbit.")

    assert report.final_verdict == "True"
    assert not any(step.startswith("search_planning") for step, _ in gonka.calls)
    assert not any(step.startswith("claim_extraction") for step, _ in gonka.calls)
    assert len(search_provider.queries) == 3


def test_independent_verifiers_run_concurrently():
    gonka = CoordinatedVerifierGonkaClient(
        {
            "claim_extraction": ['{"claims":["NASA confirms DART changed an asteroid orbit"],"not_verifiable_reason":""}'],
            "search_planning": [
                '{"general_query":"NASA DART orbit","official_source_query":"NASA DART official","supporting_evidence_query":"NASA DART evidence","contradicting_evidence_query":"NASA DART false","date_context_query":"NASA DART date","old_news_or_misinformation_query":"NASA DART old news"}'
            ],
            "verifier_1": [
                '{"verdict":"true","support_score":91,"confidence":82,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Supported.","missing_information":[]}'
            ],
            "verifier_2": [
                '{"verdict":"true","support_score":89,"confidence":80,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Supported.","missing_information":[]}'
            ],
        }
    )
    pipeline = TextFactCheckPipeline(
        make_config(),
        gonka,
        FakeSearchProvider(),
        FakeEvidenceProcessor([evidence_item()]),
    )

    report = pipeline.verify(text="NASA confirms DART changed an asteroid's orbit.")

    assert report.final_verdict == "True"


def test_configured_fallback_runs_in_parallel_as_a_standby_verifier():
    config = replace(make_config(), gonka_fallback_model="model-fallback")
    gonka = CoordinatedThreeVerifierGonkaClient(
        {
            "claim_extraction": ['{"claims":["NASA confirms DART changed an asteroid orbit"],"not_verifiable_reason":""}'],
            "search_planning": [
                '{"general_query":"NASA DART orbit","official_source_query":"NASA DART official","supporting_evidence_query":"NASA DART evidence","contradicting_evidence_query":"NASA DART false","date_context_query":"NASA DART date","old_news_or_misinformation_query":"NASA DART old news"}'
            ],
            "verifier_1": [
                '{"verdict":"true","support_score":92,"confidence":85,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Supported.","missing_information":[]}'
            ],
            "verifier_2": [
                '{"verdict":"true","support_score":90,"confidence":82,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Supported.","missing_information":[]}'
            ],
            "verifier_fallback": [
                '{"verdict":"true","support_score":94,"confidence":88,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Supported.","missing_information":[]}'
            ],
        }
    )
    pipeline = TextFactCheckPipeline(
        config,
        gonka,
        FakeSearchProvider(),
        FakeEvidenceProcessor([evidence_item()]),
    )

    report = pipeline.verify(text="NASA confirms DART changed an asteroid's orbit.")

    assert report.final_verdict == "True"
    assert ("verifier_fallback", "model-fallback") in gonka.calls
    assert any(trace.step_name == "verifier_fallback" for trace in report.gonka_trace)


def test_duckduckgo_search_follows_redirects_and_parses_nasa_result(monkeypatch):
    html = """
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.nasa.gov%2Fnews-release%2Fdart-result">
        NASA confirms DART result
      </a>
      <div class="result__snippet">NASA confirmed the asteroid orbit changed.</div>
    </div>
    """

    class FakeResponse:
        text = html

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        assert kwargs["follow_redirects"] is True
        assert "Mozilla/5.0" in kwargs["headers"]["User-Agent"]
        return FakeResponse()

    monkeypatch.setattr("services.search_provider.httpx.get", fake_get)

    results = duckduckgo_search("NASA confirms DART", max_results=3, timeout=12)

    assert [item.url for item in results] == ["https://www.nasa.gov/news-release/dart-result"]


def test_search_provider_falls_back_to_bing_when_duckduckgo_is_challenged(monkeypatch):
    provider = SearchProvider(make_config())
    expected = SearchResult(
        title="NASA DART result",
        url="https://www.nasa.gov/news-release/dart-result",
        snippet="NASA confirmed the orbit changed.",
    )
    monkeypatch.setattr("services.search_provider.ddgs_search", lambda *args: [])
    monkeypatch.setattr("services.search_provider.duckduckgo_search", lambda *args: [])
    monkeypatch.setattr("services.search_provider.bing_search", lambda *args: [expected])

    assert provider.search("NASA DART", max_results=3) == [expected]


def test_ddgs_search_maps_metasearch_results(monkeypatch):
    class FakeDDGS:
        def __init__(self, timeout):
            assert timeout == 12

        def text(self, query, **kwargs):
            assert query == "NASA DART"
            assert kwargs["backend"] == "auto"
            return [
                {
                    "title": "NASA DART result",
                    "href": "https://www.nasa.gov/dart",
                    "body": "NASA confirmed the orbit changed.",
                }
            ]

    monkeypatch.setattr("services.search_provider.DDGS", FakeDDGS)

    results = ddgs_search("NASA DART", max_results=3, timeout=12)

    assert results == [
        SearchResult(
            title="NASA DART result",
            url="https://www.nasa.gov/dart",
            snippet="NASA confirmed the orbit changed.",
        )
    ]


def test_bing_search_parses_standard_results(monkeypatch):
    html = """
    <ol id="b_results">
      <li class="b_algo">
        <h2><a href="https://www.bing.com/ck/a?u=a1aHR0cHM6Ly93d3cubmFzYS5nb3Yv">NASA DART result</a></h2>
        <div class="b_caption"><p>NASA confirmed the orbit changed.</p></div>
      </li>
    </ol>
    """

    class FakeResponse:
        text = html

        def raise_for_status(self):
            return None

    monkeypatch.setattr("services.search_provider.httpx.get", lambda *args, **kwargs: FakeResponse())

    results = bing_search("NASA DART", max_results=3, timeout=12)

    assert results[0].url == "https://www.nasa.gov/"
    assert results[0].snippet == "NASA confirmed the orbit changed."


def test_search_many_records_provider_failures(monkeypatch):
    provider = SearchProvider(make_config())

    def fail_search(query, max_results=5):
        raise SearchProviderError(f"DuckDuckGo search failed for {query}")

    monkeypatch.setattr(provider, "search", fail_search)

    assert provider.search_many(["query one", "query two"]) == []
    assert len(provider.last_errors) == 2


def test_search_many_runs_independent_queries_concurrently(monkeypatch):
    provider = SearchProvider(make_config())

    def slow_search(query, max_results=5):
        time.sleep(0.15)
        return [SearchResult(title=query, url=f"https://example.com/{query}", snippet=query)]

    monkeypatch.setattr(provider, "search", slow_search)

    started = time.perf_counter()
    results = provider.search_many(["one", "two", "three"], max_results_per_query=1)
    elapsed = time.perf_counter() - started

    assert [result.title for result in results] == ["one", "two", "three"]
    assert elapsed < 0.35


def test_evidence_uses_search_snippet_when_article_dns_resolution_fails(monkeypatch):
    def fail_extract(url):
        raise URLSafetyError("Could not resolve hostname 'www.nasa.gov'.")

    monkeypatch.setattr("services.evidence_processor.extract_article", fail_extract)

    evidence = EvidenceProcessor(max_evidence=3).build_evidence(
        [
            SearchResult(
                title="NASA confirms DART mission impact",
                url="https://www.nasa.gov/news-release/dart-result/",
                snippet="NASA confirmed that the impact changed the asteroid's orbit.",
            )
        ]
    )

    assert len(evidence) == 1
    assert evidence[0].root_domain == "nasa.gov"
    assert evidence[0].source_type == "official_government"
    assert "changed the asteroid's orbit" in evidence[0].excerpt


def test_evidence_processor_rejects_results_missing_a_distinctive_claim_anchor(monkeypatch):
    def fail_extract(url):
        raise URLSafetyError("Offline test uses search snippets.")

    monkeypatch.setattr("services.evidence_processor.extract_article", fail_extract)
    claim = "The Zorbax Institute published a clinical trial about diabetes."
    evidence = EvidenceProcessor(max_evidence=3).build_evidence(
        [
            SearchResult(
                title="ClinicalTrials.gov",
                url="https://clinicaltrials.gov/",
                snippet="A registry of publicly and privately supported clinical studies.",
            ),
            SearchResult(
                title="Zorbax Institute trial questioned",
                url="https://reuters.com/world/zorbax-trial",
                snippet="No record supports the claimed Zorbax Institute diabetes trial.",
            ),
        ],
        claim=claim,
    )

    assert [item.root_domain for item in evidence] == ["reuters.com"]


def test_evidence_processor_fetches_candidate_pages_concurrently(monkeypatch):
    def slow_extract(url):
        time.sleep(0.15)
        return ArticleContent(url=url, title=url, text=f"Evidence from {url}")

    monkeypatch.setattr("services.evidence_processor.extract_article", slow_extract)
    results = [
        SearchResult(title="One", url="https://reuters.com/one", snippet="one"),
        SearchResult(title="Two", url="https://apnews.com/two", snippet="two"),
        SearchResult(title="Three", url="https://nasa.gov/three", snippet="three"),
    ]

    started = time.perf_counter()
    evidence = EvidenceProcessor(max_evidence=3).build_evidence(results)
    elapsed = time.perf_counter() - started

    assert len(evidence) == 3
    assert elapsed < 0.35


def test_claim_anchor_normalizes_english_possessives():
    assert "malaysia" in distinctive_claim_anchors("Malaysia's official currency is the ringgit.")


def test_claim_anchors_find_latin_acronyms_next_to_chinese_text():
    anchors = distinctive_claim_anchors("美国国家航空航天局证实，NASA的DART任务改变了轨道。")

    assert anchors == ("nasa", "dart")


def test_evidence_with_multiple_claim_anchors_rejects_topic_only_pages(monkeypatch):
    def fail_extract(url):
        raise URLSafetyError("Offline test uses search snippets.")

    monkeypatch.setattr("services.evidence_processor.extract_article", fail_extract)
    evidence = EvidenceProcessor(max_evidence=3).build_evidence(
        [
            SearchResult(
                title="NASA homepage",
                url="https://www.nasa.gov/",
                snippet="NASA missions and agency news.",
            ),
            SearchResult(
                title="NASA DART changed an asteroid orbit",
                url="https://science.nasa.gov/dart-result/",
                snippet="NASA confirmed DART changed the orbit of Dimorphos.",
            ),
        ],
        claim="NASA confirms its DART mission changed an asteroid's orbit.",
    )

    assert [item.url for item in evidence] == ["https://science.nasa.gov/dart-result/"]


def test_verifier_payload_is_compact_and_keeps_auditable_evidence_fields():
    evidence = evidence_item(excerpt="x" * 1200)
    payload = build_verifier_payload(
        "A test claim",
        [evidence],
        assess_source_credibility([evidence]),
    )

    assert set(payload["evidence"][0]) == {
        "id",
        "title",
        "domain",
        "publisher",
        "published_date",
        "excerpt",
        "source_type",
        "source_quality",
    }
    assert len(payload["evidence"][0]["excerpt"]) == 700
    assert "url" not in payload["evidence"][0]
    assert "retrieved_at" not in payload["evidence"][0]


def test_verifier_uses_full_gonka_reasoning_output_budget():
    gonka = FakeGonkaClient(
        {
            "verifier_1": [
                '{"verdict":"true","support_score":90,"confidence":80,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Supported.","missing_information":[]}'
            ]
        }
    )
    evidence = evidence_item()
    pipeline = TextFactCheckPipeline(
        make_config(),
        gonka,
        FakeSearchProvider(),
        FakeEvidenceProcessor([evidence]),
    )

    _, _, succeeded = pipeline._verify_with_model(
        step_name="verifier_1",
        model_id="model-a",
        prompt_name="evidence_verifier.txt",
        claim="A test claim",
        evidence=[evidence],
        source_credibility=assess_source_credibility([evidence]),
    )

    assert succeeded is True
    assert gonka.max_tokens_by_step["verifier_1"] == 4096


def test_text_pipeline_reports_search_outage_as_a_limitation():
    gonka = FakeGonkaClient(
        {
            "claim_extraction": ['{"claims":["NASA confirms DART changed an asteroid orbit"],"not_verifiable_reason":""}'],
            "search_planning": [
                '{"general_query":"NASA DART orbit","official_source_query":"NASA DART official","supporting_evidence_query":"NASA DART evidence","contradicting_evidence_query":"NASA DART false","date_context_query":"NASA DART date","old_news_or_misinformation_query":"NASA DART old news"}'
            ],
            "verifier_1": [
                '{"verdict":"unverified","support_score":0,"confidence":0,"supporting_evidence":[],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"No evidence supplied.","missing_information":[]}'
            ],
            "verifier_2": [
                '{"verdict":"unverified","support_score":0,"confidence":0,"supporting_evidence":[],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"No evidence supplied.","missing_information":[]}'
            ],
        }
    )
    pipeline = TextFactCheckPipeline(
        make_config(),
        gonka,
        FailedSearchProvider(),
        FakeEvidenceProcessor([]),
    )

    report = pipeline.verify(text="NASA confirms DART changed an asteroid's orbit.")

    assert any("Web search failed" in item for item in report.limitations)
    assert report.concise_explanation.startswith("Web search was unavailable")
    assert not any(step.startswith("verifier_") for step, _ in gonka.calls)


def test_claim_timeout_uses_input_fallback_without_repeating_timeout():
    gonka = TimeoutClaimGonkaClient(
        {
            "search_planning": [
                '{"general_query":"rail line 2026","official_source_query":"rail line official","supporting_evidence_query":"rail line evidence","contradicting_evidence_query":"rail line false","date_context_query":"rail line date","old_news_or_misinformation_query":"rail line old"}'
            ],
            "verifier_1": [
                '{"verdict":"true","support_score":90,"confidence":80,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Evidence supports the claim.","missing_information":[]}'
            ],
            "verifier_2": [
                '{"verdict":"true","support_score":88,"confidence":75,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Independent review supports it.","missing_information":[]}'
            ],
        }
    )
    pipeline = TextFactCheckPipeline(
        make_config(),
        gonka,
        FakeSearchProvider(),
        FakeEvidenceProcessor([evidence_item()]),
    )

    report = pipeline.verify(text="The city opened a new rail line in 2026.")

    claim_calls = [step for step, _ in gonka.calls if step.startswith("claim_extraction")]
    assert claim_calls == ["claim_extraction"]
    assert report.extracted_claim == "The city opened a new rail line in 2026."
    assert any("Claim extraction timed out" in item for item in report.limitations)


def test_one_remaining_verifier_is_not_enough_for_a_firm_verdict():
    gonka = FailingFirstVerifierGonkaClient(
        {
            "claim_extraction": ['{"claims":["NASA confirms DART changed an asteroid orbit"],"not_verifiable_reason":""}'],
            "search_planning": [
                '{"general_query":"NASA DART orbit","official_source_query":"NASA DART official","supporting_evidence_query":"NASA DART evidence","contradicting_evidence_query":"NASA DART false","date_context_query":"NASA DART date","old_news_or_misinformation_query":"NASA DART old news"}'
            ],
            "verifier_2": [
                '{"verdict":"true","support_score":92,"confidence":84,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"The official evidence supports the claim.","missing_information":[]}'
            ],
        }
    )
    pipeline = TextFactCheckPipeline(
        make_config(),
        gonka,
        FakeSearchProvider(),
        FakeEvidenceProcessor([evidence_item()]),
    )

    report = pipeline.verify(text="NASA confirms DART changed an asteroid's orbit.")

    assert report.final_verdict == "Unverified"
    assert len(report.verifier_outputs) == 1
    assert report.verifier_outputs[0].model_id == "model-b"
    assert [step for step, _ in gonka.calls if step.startswith("verifier_1")] == ["verifier_1"]
    assert any("Verifier 1 failed" in item for item in report.limitations)


def test_fallback_output_is_retained_but_cannot_form_a_verdict_alone():
    config = replace(make_config(), gonka_fallback_model="model-fallback")
    gonka = FailingPrimaryVerifiersGonkaClient(
        {
            "claim_extraction": ['{"claims":["NASA confirms DART changed an asteroid orbit"],"not_verifiable_reason":""}'],
            "search_planning": [
                '{"general_query":"NASA DART orbit","official_source_query":"NASA DART official","supporting_evidence_query":"NASA DART evidence","contradicting_evidence_query":"NASA DART false","date_context_query":"NASA DART date","old_news_or_misinformation_query":"NASA DART old news"}'
            ],
            "verifier_fallback": [
                '{"verdict":"true","support_score":94,"confidence":88,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"The official evidence supports the claim.","missing_information":[]}'
            ],
        }
    )
    pipeline = TextFactCheckPipeline(
        config,
        gonka,
        FakeSearchProvider(),
        FakeEvidenceProcessor([evidence_item()]),
    )

    report = pipeline.verify(text="NASA confirms DART changed an asteroid's orbit.")

    assert report.final_verdict == "Unverified"
    assert len(report.verifier_outputs) == 1
    assert report.verifier_outputs[0].model_id == "model-fallback"
    assert ("verifier_fallback", "model-fallback") in gonka.calls
    assert any(trace.step_name == "verifier_fallback" and trace.success for trace in report.gonka_trace)


def test_failed_models_receive_one_quorum_recovery_attempt():
    config = replace(make_config(), gonka_fallback_model="model-fallback")
    gonka = RecoveringQuorumGonkaClient(
        {
            "claim_extraction": [
                '{"claims":["NASA confirms DART changed an asteroid orbit"],"not_verifiable_reason":""}'
            ],
            "search_planning": [
                '{"general_query":"NASA DART orbit","official_source_query":"NASA DART official","supporting_evidence_query":"NASA DART evidence","contradicting_evidence_query":"NASA DART false","date_context_query":"NASA DART date","old_news_or_misinformation_query":"NASA DART old news"}'
            ],
            "verifier_1": [
                '{"verdict":"true","support_score":93,"confidence":86,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Supported.","missing_information":[]}'
            ],
            "verifier_2": [
                '{"verdict":"true","support_score":91,"confidence":84,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Supported.","missing_information":[]}'
            ],
            "verifier_fallback": [
                '{"verdict":"true","support_score":94,"confidence":88,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Supported.","missing_information":[]}'
            ],
        }
    )
    pipeline = TextFactCheckPipeline(
        config,
        gonka,
        FakeSearchProvider(),
        FakeEvidenceProcessor([evidence_item()]),
    )

    report = pipeline.verify(text="NASA confirms DART changed an asteroid's orbit.")

    assert report.final_verdict == "True"
    assert len(report.verifier_outputs) == 3
    assert ("verifier_1_recovery", "model-a") in gonka.calls
    assert ("verifier_2_recovery", "model-b") in gonka.calls
    assert any("quorum recovery" in item for item in report.limitations)


def test_visible_browser_demo_receives_searches_and_evidence_urls():
    browser = FakeVisibleBrowser()
    gonka = FakeGonkaClient(
        {
            "claim_extraction": ['{"claims":["The city opened a new rail line in 2026"],"not_verifiable_reason":""}'],
            "search_planning": [
                '{"general_query":"rail line 2026","official_source_query":"rail line official","supporting_evidence_query":"rail line evidence","contradicting_evidence_query":"rail line false","date_context_query":"rail line date","old_news_or_misinformation_query":"rail line old"}'
            ],
            "verifier_1": [
                '{"verdict":"true","support_score":90,"confidence":80,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Evidence supports the claim.","missing_information":[]}'
            ],
            "verifier_2": [
                '{"verdict":"true","support_score":88,"confidence":75,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Independent review supports it.","missing_information":[]}'
            ],
        }
    )
    pipeline = TextFactCheckPipeline(
        make_config(),
        gonka,
        FakeSearchProvider(),
        FakeEvidenceProcessor([evidence_item(url="https://example.com/evidence")]),
        browser_demo=browser,
    )

    pipeline.verify(text="The city opened a new rail line in 2026.")

    assert browser.searches
    assert "rail line 2026" in browser.searches[0]
    assert browser.urls == ["https://example.com/evidence"]


def test_news_url_extraction(monkeypatch):
    def fake_extract_article(url: str) -> ArticleContent:
        return ArticleContent(url=url, title="News title", text="The mayor signed the bill in 2026.")

    monkeypatch.setattr("pipeline.text_pipeline.extract_article", fake_extract_article)
    config = make_config()
    gonka = FakeGonkaClient(
        {
            "claim_extraction": ['{"claims":["The mayor signed the bill in 2026"],"not_verifiable_reason":""}'],
            "search_planning": [
                '{"general_query":"mayor bill","official_source_query":"mayor bill official","supporting_evidence_query":"mayor bill evidence","contradicting_evidence_query":"mayor bill false","date_context_query":"mayor bill 2026","old_news_or_misinformation_query":"mayor bill old"}'
            ],
            "verifier_1": [
                '{"verdict":"mostly_true","support_score":76,"confidence":65,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Mostly supported.","missing_information":[]}'
            ],
            "verifier_2": [
                '{"verdict":"mostly_true","support_score":78,"confidence":70,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Mostly supported.","missing_information":[]}'
            ],
        }
    )
    pipeline = TextFactCheckPipeline(config, gonka, FakeSearchProvider(), FakeEvidenceProcessor([evidence_item()]))

    report = pipeline.verify(article_url="https://news.example/article")

    assert report.extracted_claim == "The mayor signed the bill in 2026"
    assert report.final_verdict == "Mostly True"


def test_screenshot_ocr(monkeypatch):
    monkeypatch.setattr("services.image_processor.pytesseract.image_to_string", lambda image: "Visible claim text")

    processed = process_image(make_png_bytes(), "image/png")

    assert processed.ocr_text == "Visible claim text"


def test_image_without_caption_or_text(monkeypatch):
    monkeypatch.setattr("services.image_processor.pytesseract.image_to_string", lambda image: "")
    pipeline = ImageFactCheckPipeline(FakeTextPipeline(), FakeGonkaClient(), vision_model_id="")

    report = pipeline.verify(image_bytes=make_png_bytes(), mime_type="image/png", caption_or_claim="")

    assert report.final_verdict == "Insufficient Evidence"
    assert "caption or contextual claim" in report.concise_explanation


def test_missing_exif_is_neutral(monkeypatch):
    monkeypatch.setattr("services.image_processor.pytesseract.image_to_string", lambda image: "")

    processed = process_image(make_png_bytes(), "image/png")

    assert processed.exif_summary == {}


def test_duplicate_source_removal():
    results = [
        SearchResult(title="A", url="https://example.com/story#section", snippet="one"),
        SearchResult(title="A", url="https://example.com/story", snippet="two"),
    ]

    unique = dedupe_search_results(results)

    assert len(unique) == 1


def test_near_duplicate_article_removal():
    first = evidence_item(evidence_id="E1", quality=0.5, title="Same event happened today")
    second = evidence_item(
        evidence_id="E2",
        url="https://apnews.com/world/test",
        title="Same event happened today",
        quality=0.78,
        excerpt=first.excerpt,
    )

    deduped = remove_near_duplicates([first, second])

    assert len(deduped) == 1
    assert deduped[0].url == "https://apnews.com/world/test"


def test_invalid_evidence_id_rejected():
    data = {
        "verdict": "true",
        "support_score": 90,
        "confidence": 80,
        "supporting_evidence": ["E999"],
        "contradicting_evidence": [],
        "context_mismatch": False,
        "reasoning_summary": "Bad citation.",
        "missing_information": [],
    }

    with pytest.raises(ValueError, match="unknown Evidence IDs"):
        validate_verifier_output(data, {"E1", "E2"})


def test_inconsistent_true_verdict_and_low_support_score_is_rejected():
    data = {
        "verdict": "true",
        "support_score": 5,
        "confidence": 5,
        "supporting_evidence": ["E1", "E2"],
        "contradicting_evidence": [],
        "context_mismatch": False,
        "reasoning_summary": "Both official sources directly support the claim.",
        "missing_information": [],
    }

    with pytest.raises(ValueError, match="inconsistent with verdict"):
        validate_verifier_output(data, {"E1", "E2"})


def test_unverified_verdict_requires_neutral_support_score():
    data = {
        "verdict": "unverified",
        "support_score": 0,
        "confidence": 90,
        "supporting_evidence": [],
        "contradicting_evidence": [],
        "context_mismatch": False,
        "reasoning_summary": "The retrieved pages are unrelated.",
        "missing_information": ["Relevant evidence"],
    }

    with pytest.raises(ValueError, match="neutral support_score"):
        validate_verifier_output(data, {"E1", "E2"})


def test_context_mismatch_requires_misleading_verdict():
    data = {
        "verdict": "false",
        "support_score": 0,
        "confidence": 90,
        "supporting_evidence": [],
        "contradicting_evidence": ["E1"],
        "context_mismatch": True,
        "reasoning_summary": "The context differs.",
        "missing_information": [],
    }

    with pytest.raises(ValueError, match="requires a misleading verdict"):
        validate_verifier_output(data, {"E1", "E2"})


def test_inconsistent_verifier_output_is_retried_before_consensus():
    gonka = FakeGonkaClient(
        {
            "claim_extraction": ['{"claims":["NASA confirms DART changed an asteroid orbit"],"not_verifiable_reason":""}'],
            "search_planning": [
                '{"general_query":"NASA DART orbit","official_source_query":"NASA DART official","supporting_evidence_query":"NASA DART evidence","contradicting_evidence_query":"NASA DART false","date_context_query":"NASA DART date","old_news_or_misinformation_query":"NASA DART old news"}'
            ],
            "verifier_1": [
                '{"verdict":"true","support_score":5,"confidence":5,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"The source supports the claim.","missing_information":[]}',
                '{"verdict":"true","support_score":92,"confidence":82,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"The source supports the claim.","missing_information":[]}',
            ],
            "verifier_2": [
                '{"verdict":"true","support_score":90,"confidence":80,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"The source supports the claim.","missing_information":[]}'
            ],
        }
    )
    pipeline = TextFactCheckPipeline(
        make_config(),
        gonka,
        FakeSearchProvider(),
        FakeEvidenceProcessor([evidence_item()]),
    )

    report = pipeline.verify(text="NASA confirms DART changed an asteroid's orbit.")

    assert report.final_verdict == "True"
    assert ("verifier_1_retry", "model-a") in gonka.calls


def test_exhausted_format_retries_do_not_trigger_a_second_recovery_layer():
    invalid = (
        '{"verdict":"true","support_score":5,"confidence":5,'
        '"supporting_evidence":["E1"],"contradicting_evidence":[],'
        '"context_mismatch":false,"reasoning_summary":"Invalid score.",'
        '"missing_information":[]}'
    )
    gonka = FakeGonkaClient(
        {
            "verifier_1": [invalid, invalid],
            "verifier_2": [
                '{"verdict":"true","support_score":90,"confidence":80,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":false,"reasoning_summary":"Supported.","missing_information":[]}'
            ],
        }
    )
    pipeline = TextFactCheckPipeline(
        make_config(),
        gonka,
        FakeSearchProvider(),
        FakeEvidenceProcessor([evidence_item()]),
        use_ai_search_planning=False,
        use_ai_claim_extraction=False,
    )

    report = pipeline.verify(text="A test claim.")

    assert report.final_verdict == "Unverified"
    assert [step for step, _ in gonka.calls if step.startswith("verifier_1")] == [
        "verifier_1",
        "verifier_1_retry",
    ]
    assert any("format validation" in item for item in report.limitations)


def test_two_agreeing_models():
    result = build_consensus([verifier(), verifier(support_score=92, confidence=70)], [evidence_item()])

    assert result.final_verdict == "True"
    assert result.confidence_score >= 75


def test_single_successful_verifier_has_capped_confidence():
    result = build_consensus(
        [verifier(support_score=95, confidence=95)],
        [evidence_item()],
    )

    assert result.final_verdict == "True"
    assert result.confidence_score <= 70


def test_single_successful_verifier_cannot_form_three_model_consensus():
    result = build_consensus(
        [verifier(support_score=95, confidence=95)],
        [evidence_item()],
        expected_verifier_count=3,
    )

    assert result.final_verdict == "Unverified"
    assert result.truth_score == 50
    assert result.confidence_score <= 35


def test_one_false_and_one_unverified_vote_remain_unverified():
    result = build_consensus(
        [
            verifier(verdict="false", support_score=0, confidence=95, support=[], contradict=["E1"]),
            verifier(verdict="unverified", support_score=50, confidence=80, support=[]),
        ],
        [evidence_item()],
        expected_verifier_count=3,
    )

    assert result.final_verdict == "Unverified"
    assert result.truth_score == 50


def test_three_model_majority_resists_one_false_outlier():
    result = build_consensus(
        [
            verifier(verdict="true", support_score=94, confidence=85),
            verifier(verdict="true", support_score=91, confidence=82),
            verifier(
                verdict="false",
                support_score=8,
                confidence=80,
                support=[],
                contradict=["E1"],
            ),
        ],
        [evidence_item()],
    )

    assert result.final_verdict == "True"
    assert result.truth_score >= 85


def test_three_model_majority_resists_one_true_outlier():
    result = build_consensus(
        [
            verifier(verdict="false", support_score=4, confidence=85, support=[], contradict=["E1"]),
            verifier(verdict="false", support_score=9, confidence=82, support=[], contradict=["E1"]),
            verifier(verdict="true", support_score=96, confidence=80),
        ],
        [evidence_item()],
    )

    assert result.final_verdict == "False"
    assert result.truth_score <= 24


def test_unavailable_third_verifier_reduces_confidence():
    result = build_consensus(
        [
            verifier(verdict="true", support_score=95, confidence=95),
            verifier(verdict="true", support_score=95, confidence=95),
        ],
        [evidence_item()],
        expected_verifier_count=3,
    )

    assert result.final_verdict == "True"
    assert result.confidence_score == 90


def test_one_unverified_model_does_not_overturn_two_true_models():
    result = build_consensus(
        [
            verifier(verdict="true", support_score=94, confidence=88),
            verifier(verdict="true", support_score=91, confidence=84),
            verifier(
                verdict="unverified",
                support_score=50,
                confidence=20,
                support=[],
            ),
        ],
        [evidence_item()],
    )

    assert result.final_verdict == "True"
    assert result.confidence_score < 90


def test_majority_unverified_models_keep_claim_unverified():
    result = build_consensus(
        [
            verifier(verdict="true", support_score=90, confidence=80),
            verifier(verdict="unverified", support_score=50, confidence=30, support=[]),
            verifier(verdict="unverified", support_score=50, confidence=25, support=[]),
        ],
        [evidence_item()],
    )

    assert result.final_verdict == "Unverified"
    assert result.truth_score == 50
    assert result.confidence_score <= 40


def test_one_context_mismatch_does_not_overturn_two_matching_reviews():
    result = build_consensus(
        [
            verifier(verdict="true", support_score=94, confidence=88),
            verifier(verdict="true", support_score=91, confidence=84),
            verifier(
                verdict="misleading",
                support_score=55,
                confidence=70,
                context_mismatch=True,
            ),
        ],
        [evidence_item()],
    )

    assert result.final_verdict == "True"


def test_mixed_support_score_uses_consistent_misleading_label():
    result = build_consensus(
        [
            verifier(verdict="misleading", support_score=58, confidence=80),
            verifier(verdict="misleading", support_score=62, confidence=78),
        ],
        [evidence_item()],
    )

    assert result.final_verdict == "Misleading"


def test_deterministic_search_removes_reporting_language_from_false_claim():
    queries = deterministic_search_queries(
        "NASA confirms its DART mission made the asteroid Dimorphos a threat to Earth."
    )

    assert queries.general_query == "NASA DART mission asteroid Dimorphos threat Earth"
    assert queries.official_source_query == "NASA DART mission asteroid Dimorphos threat Earth official"


def test_deterministic_search_preserves_chinese_claim_text():
    claim = "美国国家航空航天局证实，DART任务改变了小行星的轨道。"

    queries = deterministic_search_queries(claim)

    assert queries.general_query == "NASA DART"


def test_deterministic_search_uses_english_alias_for_chandrayaan():
    queries = deterministic_search_queries("印度的月船3号于2023年8月23日成功软着陆。")

    assert queries.general_query.startswith("Chandrayaan-3 2023")


def test_deterministic_search_limits_long_claim_queries():
    queries = deterministic_search_queries(
        "The World Health Organization ended the COVID-19 global health emergency on 5 May 2023."
    )

    assert len(queries.general_query.split()) <= 8
    assert queries.general_query == "WHO ended COVID-19 global health emergency 5 May"


def test_gonka_chat_uses_deterministic_temperature():
    captured = {}
    response_body = json.dumps(
        {
            "id": "response-1",
            "object": "chat.completion",
            "created": 0,
            "model": "model-a",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
        }
    ).encode("utf-8")

    class CaptureRequestHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", "0"))
            captured.update(json.loads(self.rfile.read(content_length)))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), CaptureRequestHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    client = GonkaClient(
        replace(make_config(), gonka_base_url=f"http://127.0.0.1:{server.server_port}/v1"),
        timeout=1,
    )
    try:
        client.chat(
            step_name="test",
            model_id="model-a",
            messages=[{"role": "user", "content": "test"}],
        )
    finally:
        server.shutdown()
        server.server_close()

    assert captured["temperature"] == 0.0


def test_gonka_chat_enforces_total_request_deadline():
    response_body = json.dumps(
        {
            "id": "response-slow",
            "object": "chat.completion",
            "created": 0,
            "model": "model-a",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "too late"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    ).encode("utf-8")

    class SlowResponseHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            try:
                for byte in response_body:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(0.01)
            except OSError:
                pass

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowResponseHandler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}/v1"
    client = GonkaClient(replace(make_config(), gonka_base_url=base_url), timeout=0.1)

    started = time.perf_counter()
    try:
        with pytest.raises(GonkaCallFailed) as captured:
            client.chat(
                step_name="deadline_test",
                model_id="model-a",
                messages=[{"role": "user", "content": "test"}],
            )
    finally:
        elapsed = time.perf_counter() - started
        server.shutdown()
        server.server_close()

    assert elapsed < 0.75
    assert captured.value.trace.success is False
    assert "timeout" in str(captured.value).lower()


def test_two_disagreeing_models_trigger_judge():
    assert needs_judge(verifier(support_score=90), verifier(verdict="false", support_score=10))


def test_unverified_vote_does_not_trigger_slow_judge_call():
    assert not needs_judge(
        verifier(verdict="false", support_score=0, support=[], contradict=["E1"]),
        verifier(verdict="unverified", support_score=50, confidence=40, support=[]),
    )


def test_timed_out_verifier_model_is_not_called_again_as_judge():
    config = replace(
        make_config(),
        gonka_judge_model="model-b",
        gonka_fallback_model="model-fallback",
    )
    gonka = TimeoutSecondVerifierGonkaClient(
        {
            "verifier_1": [
                '{"verdict":"misleading","support_score":55,"confidence":85,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":true,"reasoning_summary":"Context differs.","missing_information":[]}'
            ],
            "verifier_fallback": [
                '{"verdict":"false","support_score":15,"confidence":90,"supporting_evidence":[],"contradicting_evidence":["E1"],"context_mismatch":false,"reasoning_summary":"Contradicted.","missing_information":[]}'
            ],
        }
    )
    pipeline = TextFactCheckPipeline(
        config,
        gonka,
        FakeSearchProvider(),
        FakeEvidenceProcessor([evidence_item()]),
        use_ai_search_planning=False,
        use_ai_claim_extraction=False,
    )

    report = pipeline.verify(text="A disputed claim.")

    assert not any(step == "disagreement_judge" for step, _ in gonka.calls)
    assert any("failed during its initial verifier call" in item for item in report.limitations)


def test_recovered_verifier_model_is_not_immediately_reused_as_judge():
    config = replace(make_config(), gonka_judge_model="model-b")
    gonka = RecoveringJudgeModelGonkaClient(
        {
            "verifier_1": [
                '{"verdict":"misleading","support_score":55,"confidence":85,"supporting_evidence":["E1"],"contradicting_evidence":[],"context_mismatch":true,"reasoning_summary":"Context differs.","missing_information":[]}'
            ],
            "verifier_2": [
                '{"verdict":"false","support_score":15,"confidence":90,"supporting_evidence":[],"contradicting_evidence":["E1"],"context_mismatch":false,"reasoning_summary":"Contradicted.","missing_information":[]}'
            ],
        }
    )
    pipeline = TextFactCheckPipeline(
        config,
        gonka,
        FakeSearchProvider(),
        FakeEvidenceProcessor([evidence_item()]),
        use_ai_search_planning=False,
        use_ai_claim_extraction=False,
    )

    report = pipeline.verify(text="A disputed claim.")

    assert ("verifier_2_recovery", "model-b") in gonka.calls
    assert not any(step == "disagreement_judge" for step, _ in gonka.calls)
    assert any("failed during its initial verifier call" in item for item in report.limitations)


def test_insufficient_evidence_produces_unverified():
    result = build_consensus([verifier(), verifier()], [])

    assert result.final_verdict == "Unverified"
    assert result.confidence_score == 25


def test_vision_model_unavailable_fallback(monkeypatch):
    monkeypatch.setattr("services.image_processor.pytesseract.image_to_string", lambda image: "Claim text")
    pipeline = ImageFactCheckPipeline(FakeTextPipeline(), FailingVisionGonkaClient(), vision_model_id="model-vision")

    report = pipeline.verify(
        image_bytes=make_png_bytes(),
        mime_type="image/png",
        caption_or_claim="This shows a flood in 2026.",
    )

    assert report.image_context_assessment is not None
    assert report.image_context_assessment.verdict == "Insufficient Evidence"
    assert any("Vision model unavailable" in item for item in report.limitations)


def test_gonka_request_id_capture():
    request_id, trace_id = extract_request_trace_ids(
        {"X-Request-ID": "req-123", "Trace-ID": "trace-456"}
    )

    assert request_id == "req-123"
    assert trace_id == "trace-456"


def test_api_key_redaction():
    secret = "gonka-secret-token"

    redacted = redact_secrets(f"Authorization: Bearer {secret} GONKA_API_KEY={secret}", [secret])

    assert secret not in redacted
    assert "[REDACTED]" in redacted


def test_private_reasoning_is_stripped_before_display_or_json_parsing():
    raw = '<think>hidden reasoning</think>{"verdict":"unverified"}'

    assert strip_private_reasoning(raw) == '{"verdict":"unverified"}'
    assert parse_json_object(raw) == {"verdict": "unverified"}


def test_private_network_url_blocking():
    with pytest.raises(URLSafetyError):
        validate_public_url("http://127.0.0.1:8501/private")


def test_single_low_credibility_website_is_high_risk():
    evidence = [
        evidence_item(
            url="https://random-blog.example/story",
            title="Viral claim",
            quality=0.35,
            excerpt="A single blog repeats the claim.",
        )
    ]

    assessment = assess_source_credibility(evidence)

    assert assessment.website_risk_level == "High"
    assert assessment.independent_source_count == 1
    assert any("not corroborated" in signal for signal in assessment.risk_signals)


def test_independent_high_quality_sources_lower_website_risk():
    evidence = [
        evidence_item(evidence_id="E1", url="https://reuters.com/world/test", quality=0.78),
        evidence_item(
            evidence_id="E2",
            url="https://apnews.com/world/test",
            title="Second independent source",
            quality=0.78,
            excerpt="A second independent source supports the same timeline.",
        ),
    ]

    assessment = assess_source_credibility(evidence)

    assert assessment.website_risk_level == "Low"
    assert assessment.source_trust_score >= 75
    assert assessment.independent_source_count == 2


def test_source_credibility_can_force_unverified():
    weak_single_source = [
        evidence_item(
            url="https://unknown-site.example/story",
            title="Only source",
            quality=0.5,
            excerpt="The only available source claims this happened.",
        )
    ]
    assessment = assess_source_credibility(weak_single_source)

    result = build_consensus(
        [verifier(support_score=90, confidence=85), verifier(support_score=88, confidence=80)],
        weak_single_source,
        source_credibility=assessment,
    )

    assert assessment.website_risk_level == "High"
    assert result.final_verdict == "Unverified"
