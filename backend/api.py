from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import Any, Callable, Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from config import AppConfig, load_config
from pipeline.image_pipeline import ImageFactCheckPipeline
from pipeline.text_pipeline import ProgressCallback, TextFactCheckPipeline
from schemas.models import FactCheckReport
from services.evidence_processor import EvidenceProcessor
from services.gonka_client import GonkaClient, redact_secrets
from services.image_processor import MAX_IMAGE_BYTES
from services.visible_browser import VisibleBrowserDemo


PROJECT_ROOT = Path(__file__).resolve().parent
ReviewMode = Literal["quick", "professional"]
VerificationRunner = Callable[
    [TextFactCheckPipeline, GonkaClient, AppConfig, ProgressCallback],
    FactCheckReport,
]


class VerificationRequest(BaseModel):
    text: str = Field(default="", max_length=12_000)
    url: str = Field(default="", max_length=2048)
    mode: ReviewMode = "quick"
    show_browser: bool = False

    @model_validator(mode="after")
    def require_input(self) -> "VerificationRequest":
        if not self.text.strip() and not self.url.strip():
            raise ValueError("Enter a text claim or article URL.")
        return self


app = FastAPI(title="Verity Desk API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_error_message(exc: Exception) -> str:
    secrets = [os.getenv("GONKA_API_KEY", ""), os.getenv("TAVILY_API_KEY", "")]
    return redact_secrets(str(exc), secrets)[:1000]


def encode_event(event_type: str, data: dict[str, Any]) -> str:
    return json.dumps({"type": event_type, "data": data}, ensure_ascii=True) + "\n"


def build_text_pipeline(
    *,
    config: AppConfig,
    client: GonkaClient,
    mode: ReviewMode,
    emit: ProgressCallback,
    browser_demo: Any | None,
) -> TextFactCheckPipeline:
    is_professional = mode == "professional"
    return TextFactCheckPipeline(
        config=config,
        gonka_client=client,
        evidence_processor=EvidenceProcessor(max_evidence=12 if is_professional else 5),
        progress_callback=emit,
        browser_demo=browser_demo,
        max_results_per_query=4 if is_professional else 3,
        use_ai_search_planning=is_professional,
        use_ai_claim_extraction=is_professional,
    )


def stream_verification(
    *,
    mode: ReviewMode,
    show_browser: bool,
    runner: VerificationRunner,
) -> StreamingResponse:
    def event_stream():
        event_queue: Queue[dict[str, Any] | None] = Queue()

        def emit(stage: str, details: dict[str, Any]) -> None:
            event_queue.put(
                {
                    "stage": stage,
                    "details": details,
                    "timestamp_utc": utc_now_iso(),
                }
            )

        def run_pipeline() -> None:
            browser_demo = None
            try:
                config = load_config(PROJECT_ROOT / ".env")
                if show_browser:
                    emit("Visible browser starting", {})
                    browser_demo = VisibleBrowserDemo()
                    emit("Visible browser ready", {})

                client = GonkaClient(config)
                text_pipeline = build_text_pipeline(
                    config=config,
                    client=client,
                    mode=mode,
                    emit=emit,
                    browser_demo=browser_demo,
                )
                report = runner(text_pipeline, client, config, emit)
                event_queue.put(
                    {
                        "type": "report",
                        "data": {
                            "report": report.model_dump(mode="json"),
                            "completed_at_utc": utc_now_iso(),
                        },
                    }
                )
            except Exception as exc:
                event_queue.put(
                    {
                        "type": "error",
                        "data": {
                            "message": safe_error_message(exc),
                            "error_type": exc.__class__.__name__,
                        },
                    }
                )
            finally:
                if browser_demo is not None:
                    try:
                        browser_demo.close()
                    except Exception:
                        pass
                event_queue.put(None)

        Thread(target=run_pipeline, daemon=True, name="verity-verification").start()

        while True:
            event = event_queue.get()
            if event is None:
                break
            if "type" in event:
                yield encode_event(event["type"], event["data"])
            else:
                yield encode_event("progress", event)

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/verify/stream")
def verify_stream(request: VerificationRequest) -> StreamingResponse:
    def run(
        text_pipeline: TextFactCheckPipeline,
        client: GonkaClient,
        config: AppConfig,
        emit: ProgressCallback,
    ) -> FactCheckReport:
        del client, config, emit
        return text_pipeline.verify(text=request.text, article_url=request.url)

    return stream_verification(
        mode=request.mode,
        show_browser=request.show_browser,
        runner=run,
    )


@app.post("/api/verify/image/stream")
async def verify_image_stream(
    image: UploadFile = File(...),
    caption: str = Form(default="", max_length=12_000),
    mode: ReviewMode = Form(default="quick"),
    show_browser: bool = Form(default=False),
) -> StreamingResponse:
    image_bytes = await image.read(MAX_IMAGE_BYTES + 1)
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds the 10 MB maximum size.")
    mime_type = image.content_type or "application/octet-stream"

    def run(
        text_pipeline: TextFactCheckPipeline,
        client: GonkaClient,
        config: AppConfig,
        emit: ProgressCallback,
    ) -> FactCheckReport:
        image_pipeline = ImageFactCheckPipeline(
            text_pipeline=text_pipeline,
            gonka_client=client,
            vision_model_id=config.gonka_vision_model,
            progress_callback=emit,
        )
        return image_pipeline.verify(
            image_bytes=image_bytes,
            mime_type=mime_type,
            caption_or_claim=caption,
        )

    return stream_verification(mode=mode, show_browser=show_browser, runner=run)


frontend_dist = PROJECT_ROOT / "frontend" / "dist"
if frontend_dist.exists():
    @app.get("/{full_path:path}", include_in_schema=False)
    def frontend_app(full_path: str) -> FileResponse:
        resolved_dist = frontend_dist.resolve()
        requested_file = (resolved_dist / full_path).resolve()
        if resolved_dist in requested_file.parents and requested_file.is_file():
            return FileResponse(requested_file)
        return FileResponse(resolved_dist / "index.html")
