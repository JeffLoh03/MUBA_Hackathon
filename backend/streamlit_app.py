from __future__ import annotations

from datetime import datetime
from typing import Any

import streamlit as st

from backend.config import ConfigError, load_config
from backend.pipeline.image_pipeline import ImageFactCheckPipeline
from backend.pipeline.text_pipeline import PipelineConfigError, TextFactCheckPipeline
from backend.schemas.models import EvidenceItem, FactCheckReport
from backend.services.gonka_client import GonkaClient, GonkaClientError, redact_secrets
from backend.services.model_discovery import discover_models
from backend.services.search_provider import SearchProvider
from backend.services.visible_browser import VisibleBrowserDemo, VisibleBrowserError


st.set_page_config(
    page_title="Gonka AI Fact Checker",
    layout="wide",
)


def main() -> None:
    st.title("Gonka AI Fact Checker")
    st.caption("Evidence-based text and image context verification routed through Gonka Router.")

    try:
        config = load_config()
    except ConfigError as exc:
        st.error(str(exc))
        st.stop()

    show_browser_demo = render_sidebar(config)

    text_tab, image_tab = st.tabs(["Text / URL", "Image"])
    with text_tab:
        render_text_tab(config, show_browser_demo)
    with image_tab:
        render_image_tab(config, show_browser_demo)


def render_sidebar(config) -> bool:
    with st.sidebar:
        st.subheader("Configuration")
        st.write(f"Base URL: `{config.gonka_base_url}`")
        st.write(f"Search provider: `{config.search_provider}`")
        if not config.env_file_found:
            st.warning(".env was not found. Environment variables from the shell are being used.")

        missing = config.missing_required_values()
        if missing:
            st.error("Missing: " + ", ".join(missing))

        multi_model_issue = config.multi_model_issue()
        if multi_model_issue:
            st.warning(multi_model_issue)

        st.write("Verifier 1:", config.gonka_verify_model_1 or "Not configured")
        st.write("Verifier 2:", config.gonka_verify_model_2 or "Not configured")
        st.write("Judge:", config.gonka_judge_model or "Falls back to verifier 1")
        st.write("Decision reviewer:", config.decision_model or "Not configured")
        st.write("Vision:", config.gonka_vision_model or "OCR fallback only")

        st.subheader("Demo Mode")
        show_browser_demo = st.checkbox(
            "Show live browser window",
            value=False,
            help="Local-only demo mode. Opens Chrome/Chromium so viewers can watch searches and evidence pages.",
        )
        st.caption("Works locally. Hosted deployments usually cannot pop out a browser window.")

        if st.button("Close Demo Browser"):
            close_visible_browser()

        if st.button("Discover Gonka Models"):
            if not config.gonka_api_key:
                st.error("Set GONKA_API_KEY before model discovery.")
            else:
                try:
                    models = discover_models(GonkaClient(config))
                    st.success(f"Found {len(models)} model(s).")
                    st.code("\n".join(models), language="text")
                except GonkaClientError as exc:
                    st.error(redact_secrets(str(exc), [config.gonka_api_key, config.tavily_api_key]))
    return show_browser_demo


def render_text_tab(config, show_browser_demo: bool) -> None:
    claim_text = st.text_area(
        "Text claim",
        height=180,
        placeholder="Paste a claim, paragraph, or article excerpt.",
    )
    article_url = st.text_input("Optional article URL", placeholder="https://example.com/news/article")

    if st.button("Verify Text / URL", type="primary"):
        run_text_verification(config, claim_text, article_url, show_browser_demo)


def render_image_tab(config, show_browser_demo: bool) -> None:
    uploaded = st.file_uploader("Upload JPG, JPEG, PNG, or WEBP", type=["jpg", "jpeg", "png", "webp"])
    caption = st.text_area(
        "Caption or contextual claim",
        height=110,
        placeholder="Example: This photo shows flooding in Kuala Lumpur today.",
    )

    if uploaded is not None:
        st.image(uploaded, use_container_width=True)

    if st.button("Verify Image Context", type="primary"):
        if uploaded is None:
            st.error("Upload an image first.")
            return
        run_image_verification(
            config,
            uploaded.getvalue(),
            uploaded.type or "image/jpeg",
            caption,
            show_browser_demo,
        )


def run_text_verification(config, claim_text: str, article_url: str, show_browser_demo: bool) -> None:
    if not config.gonka_api_key:
        st.error("Set GONKA_API_KEY in .env before verification.")
        return
    timeline = st.empty()
    progress = make_progress_callback(timeline, [config.gonka_api_key, config.tavily_api_key])
    try:
        progress("Configuration checked", {"mode": "Text / URL", "reasoning_note": "Structured audit log only; hidden chain-of-thought is not displayed."})
        browser_demo = get_visible_browser_demo(show_browser_demo, progress)
        client = GonkaClient(config)
        pipeline = TextFactCheckPipeline(
            config,
            client,
            SearchProvider(config),
            progress_callback=progress,
            browser_demo=browser_demo,
        )
        with st.spinner("Retrieving evidence and asking Gonka verifier models..."):
            report = pipeline.verify(text=claim_text, article_url=article_url)
        progress("Verification finished", {"final_verdict": report.final_verdict, "truth_score": report.truth_score, "confidence_score": report.confidence_score})
        render_report(report)
    except PipelineConfigError as exc:
        progress("Verification stopped", {"safe_error_message": str(exc)})
        st.error(str(exc))
    except Exception as exc:
        progress("Verification failed", {"safe_error_message": str(exc)})
        st.error(redact_secrets(str(exc), [config.gonka_api_key, config.tavily_api_key]))


def run_image_verification(
    config,
    image_bytes: bytes,
    mime_type: str,
    caption: str,
    show_browser_demo: bool,
) -> None:
    if not config.gonka_api_key:
        st.error("Set GONKA_API_KEY in .env before verification.")
        return
    timeline = st.empty()
    progress = make_progress_callback(timeline, [config.gonka_api_key, config.tavily_api_key])
    try:
        progress("Configuration checked", {"mode": "Image", "reasoning_note": "Structured audit log only; hidden chain-of-thought is not displayed."})
        browser_demo = get_visible_browser_demo(show_browser_demo, progress)
        client = GonkaClient(config)
        text_pipeline = TextFactCheckPipeline(
            config,
            client,
            SearchProvider(config),
            progress_callback=progress,
            browser_demo=browser_demo,
        )
        image_pipeline = ImageFactCheckPipeline(
            text_pipeline,
            client,
            vision_model_id=config.gonka_vision_model,
            progress_callback=progress,
        )
        with st.spinner("Running OCR, retrieving evidence, and asking Gonka verifier models..."):
            report = image_pipeline.verify(
                image_bytes=image_bytes,
                mime_type=mime_type,
                caption_or_claim=caption,
            )
        progress("Verification finished", {"final_verdict": report.final_verdict, "truth_score": report.truth_score, "confidence_score": report.confidence_score})
        render_report(report)
    except PipelineConfigError as exc:
        progress("Verification stopped", {"safe_error_message": str(exc)})
        st.error(str(exc))
    except Exception as exc:
        progress("Verification failed", {"safe_error_message": str(exc)})
        st.error(redact_secrets(str(exc), [config.gonka_api_key, config.tavily_api_key]))


def render_report(report: FactCheckReport) -> None:
    st.subheader("Extracted Claim")
    st.write(report.extracted_claim or "Not a verifiable factual claim")
    if report.extracted_claims:
        with st.expander("All Extracted Claims"):
            for claim in report.extracted_claims:
                st.write(f"- {claim}")

    metrics = st.columns(3)
    metrics[0].metric("Final Verdict", report.final_verdict)
    metrics[1].metric("Truth Score", f"{report.truth_score}/100")
    metrics[2].metric("Confidence Score", f"{report.confidence_score}/100")

    st.subheader("Concise Explanation")
    st.write(report.concise_explanation)

    render_source_credibility(report)

    st.subheader("Supporting Evidence")
    render_evidence(report.supporting_evidence)

    st.subheader("Contradicting Evidence")
    render_evidence(report.contradicting_evidence)

    with st.expander("All Retrieved Evidence", expanded=False):
        render_evidence(report.all_evidence)

    st.subheader("Model Comparison")
    if report.verifier_outputs:
        st.dataframe([item.model_dump() for item in report.verifier_outputs], use_container_width=True)
    if report.judge_output:
        st.write("Judge output")
        st.json(report.judge_output.model_dump())

    if report.image_context_assessment:
        st.subheader("Image Context Assessment")
        st.metric("Image Verdict", report.image_context_assessment.verdict)
        st.write("OCR preview")
        st.code(report.image_context_assessment.ocr_text or "(no OCR text)", language="text")
        st.write(report.image_context_assessment.reverse_image_note)
        if report.image_context_assessment.exif_summary:
            st.json(report.image_context_assessment.exif_summary)
        else:
            st.info("No EXIF metadata was found. This is neutral, not suspicious.")

    st.subheader("Gonka Verification Trace")
    if report.gonka_trace:
        st.dataframe([item.model_dump() for item in report.gonka_trace], use_container_width=True)
    else:
        st.write("No Gonka calls were made for this result.")

    st.subheader("Limitations")
    if report.limitations:
        for limitation in report.limitations:
            st.write(f"- {limitation}")
    else:
        st.write("No additional limitations recorded.")


def render_evidence(items: list[EvidenceItem]) -> None:
    if not items:
        st.write("None.")
        return
    for item in items:
        st.markdown(f"**{item.evidence_id}. [{item.title or item.url}]({item.url})**")
        st.caption(
            f"{item.publisher} | {item.source_type} | quality {item.source_quality:.2f} | retrieved {item.retrieved_at}"
        )
        st.write(item.excerpt)


def render_source_credibility(report: FactCheckReport) -> None:
    assessment = report.source_credibility_assessment
    st.subheader("Website / Source Credibility Assessment")
    if assessment is None:
        st.write("No source credibility assessment was produced.")
        return

    metrics = st.columns(4)
    metrics[0].metric("Source Trust", f"{assessment.source_trust_score}/100")
    metrics[1].metric("Website Risk", assessment.website_risk_level)
    metrics[2].metric("Independent Sources", assessment.independent_source_count)
    metrics[3].metric("Duplicate Risk", assessment.duplicate_or_syndication_risk)

    st.write(assessment.summary)

    left, right = st.columns(2)
    with left:
        st.write("Trust Signals")
        for signal in assessment.trust_signals:
            st.write(f"- {signal}")
    with right:
        st.write("Risk Signals")
        for signal in assessment.risk_signals:
            st.write(f"- {signal}")

    if assessment.strongest_sources:
        with st.expander("Strongest Sources"):
            for source in assessment.strongest_sources:
                st.write(f"- {source}")


def make_progress_callback(placeholder, secrets: list[str]):
    events: list[dict[str, Any]] = []

    def progress(stage: str, details: dict[str, Any]) -> None:
        events.append(
            {
                "time": datetime.now().strftime("%H:%M:%S"),
                "stage": stage,
                "details": redact_payload(details, secrets),
            }
        )
        render_live_timeline(placeholder, events)

    return progress


def render_live_timeline(placeholder, events: list[dict[str, Any]]) -> None:
    with placeholder.container():
        st.subheader("Live Verification Timeline")
        st.caption(
            "Shows the auditable checking process in real time. Hidden chain-of-thought is never shown."
        )
        for index, event in enumerate(events, start=1):
            line = f"{index}. {event['time']} - {event['stage']}"
            details = event["details"]
            if isinstance(details, dict) and details:
                important = compact_event_details(details)
                st.write(f"**{line}**")
                if important:
                    st.caption(important)
                with st.expander("Details", expanded=False):
                    st.json(details)
            else:
                st.write(f"**{line}**")


def compact_event_details(details: dict[str, Any]) -> str:
    preferred_keys = [
        "model",
        "requested_model_id",
        "final_verdict",
        "verdict",
        "truth_score",
        "confidence_score",
        "support_score",
        "confidence",
        "source_trust_score",
        "website_risk_level",
        "independent_source_count",
        "evidence_count",
        "raw_result_count",
        "latency_ms",
        "request_id",
        "trace_id",
    ]
    parts = []
    for key in preferred_keys:
        value = details.get(key)
        if value not in (None, "", []):
            parts.append(f"{key}: {value}")
    return " | ".join(parts)


def redact_payload(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, dict):
        return {key: redact_payload(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_payload(item, secrets) for item in value]
    if isinstance(value, str):
        return redact_secrets(value, secrets)
    return value


def get_visible_browser_demo(enabled: bool, progress) -> VisibleBrowserDemo | None:
    if not enabled:
        return None

    try:
        existing = st.session_state.get("visible_browser_demo")
        if existing is not None and existing.is_active():
            progress("Visible browser attached", {"browser": "existing Chrome/Chromium window"})
            return existing

        browser = VisibleBrowserDemo()
        st.session_state["visible_browser_demo"] = browser
        progress(
            "Visible browser opened",
            {
                "browser": "Chrome if installed, otherwise Playwright Chromium",
                "note": "The browser shows searches and evidence pages; fact checking still uses structured evidence.",
            },
        )
        return browser
    except VisibleBrowserError as exc:
        progress("Visible browser unavailable", {"safe_error_message": str(exc)})
        st.warning(str(exc))
    except Exception as exc:
        progress("Visible browser unavailable", {"safe_error_message": str(exc)})
        st.warning(f"Could not open the visible browser demo: {exc}")
    return None


def close_visible_browser() -> None:
    browser = st.session_state.pop("visible_browser_demo", None)
    if browser is None:
        st.info("No demo browser is currently managed by the app.")
        return
    try:
        browser.close()
        st.success("Demo browser closed.")
    except Exception as exc:
        st.warning(f"Could not close demo browser cleanly: {exc}")


if __name__ == "__main__":
    main()
