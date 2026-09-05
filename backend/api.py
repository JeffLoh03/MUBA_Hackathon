from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from threading import BoundedSemaphore, Lock, Thread
from typing import Any, Callable, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from backend.config import AppConfig, load_config
from backend.auth import (
    AuthStore, Credentials, SESSION_COOKIE, User, is_direct_loopback,
    require_same_origin, secure_cookie_enabled, set_session_cookie,
)
from backend.database import AuditStore
from backend.pipeline.image_pipeline import ImageFactCheckPipeline
from backend.pipeline.text_pipeline import ProgressCallback, TextFactCheckPipeline
from backend.schemas.models import FactCheckReport
from backend.services.evidence_processor import EvidenceProcessor
from backend.services.gonka_client import GonkaClient, redact_secrets
from backend.services.image_processor import MAX_IMAGE_BYTES
from backend.services.visible_browser import VisibleBrowserDemo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_CONCURRENT_VERIFICATIONS = 4
verification_slots = BoundedSemaphore(MAX_CONCURRENT_VERIFICATIONS)
audit_store = AuditStore()
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


app = FastAPI(title="Verity Desk API", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@app.middleware("http")
async def private_api_responses(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def auth_store() -> AuthStore:
    return AuthStore(audit_store)


def require_user(request: Request) -> User:
    user = auth_store().session_user(request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return user


def allow_verification(request: Request, user: User = Depends(require_user)) -> User:
    auth_store().consume_rate_limit(f"verify:{user.id}", limit=20, window_seconds=3600)
    return user


def authentication_status(user: User | None) -> dict[str, Any]:
    return {
        "authenticated": user is not None,
        "setup_required": auth_store().setup_required(),
        "user": user.public() if user else None,
    }


def limit_login_attempts(request: Request) -> None:
    address = request.client.host if request.client else "unknown"
    auth_store().consume_rate_limit(f"login:{address}", limit=10, window_seconds=900)


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict[str, Any]:
    user = auth_store().session_user(request.cookies.get(SESSION_COOKIE))
    return authentication_status(user)


@app.post("/api/auth/setup")
def auth_setup(credentials: Credentials, request: Request, response: Response) -> dict[str, Any]:
    if not is_direct_loopback(request):
        raise HTTPException(status_code=403, detail="Create the first account directly on the server using localhost.")
    limit_login_attempts(request)
    store = auth_store()
    user = store.create_first_user(credentials)
    set_session_cookie(response, store.create_session(user, request.cookies.get(SESSION_COOKIE)))
    return authentication_status(user)


@app.post("/api/auth/login")
def auth_login(credentials: Credentials, request: Request, response: Response) -> dict[str, Any]:
    limit_login_attempts(request)
    store = auth_store()
    user = store.authenticate(credentials)
    if user is None:
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    set_session_cookie(response, store.create_session(user, request.cookies.get(SESSION_COOKIE)))
    return authentication_status(user)


@app.post("/api/auth/logout", status_code=204)
def auth_logout(request: Request) -> Response:
    auth_store().revoke_session(request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=secure_cookie_enabled(), samesite="strict")
    return response


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
        enable_deep_review=is_professional,
    )


def stream_verification(
    *,
    mode: ReviewMode,
    show_browser: bool,
    runner: VerificationRunner,
    input_type: str,
    input_text: str = "",
    article_url: str = "",
    image_name: str = "",
    owner_user_id: str,
) -> StreamingResponse:
    if not verification_slots.acquire(blocking=False):
        raise HTTPException(
            status_code=503,
            detail="The verification service is busy. Try again shortly.",
        )

    try:
        run_id = audit_store.create_run(
            input_type=input_type,
            input_text=input_text,
            article_url=article_url,
            image_name=image_name,
            mode=mode,
            owner_user_id=owner_user_id,
        )
    except Exception as exc:
        verification_slots.release()
        raise HTTPException(status_code=500, detail="Could not create the verification audit record.") from exc

    def event_stream():
        event_queue: Queue[dict[str, Any] | None] = Queue()
        sequence_lock = Lock()
        sequence_number = 0

        def emit(stage: str, details: dict[str, Any]) -> None:
            nonlocal sequence_number
            event = {
                "stage": stage,
                "details": details,
                "timestamp_utc": utc_now_iso(),
            }
            with sequence_lock:
                sequence_number += 1
                audit_store.append_event(
                    run_id,
                    sequence_number=sequence_number,
                    stage=stage,
                    timestamp_utc=event["timestamp_utc"],
                    details=details,
                )
            event_queue.put(event)

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
                completed_at_utc = utc_now_iso()
                audit_store.complete_run(run_id, report, completed_at_utc)
                event_queue.put(
                    {
                        "type": "report",
                        "data": {
                            "run_id": run_id,
                            "report": report.model_dump(mode="json"),
                            "completed_at_utc": completed_at_utc,
                        },
                    }
                )
            except Exception as exc:
                message = safe_error_message(exc)
                try:
                    audit_store.fail_run(run_id, message, utc_now_iso())
                except Exception:
                    pass
                event_queue.put(
                    {
                        "type": "error",
                        "data": {
                            "run_id": run_id,
                            "message": message,
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
                verification_slots.release()

        try:
            Thread(target=run_pipeline, daemon=True, name="verity-verification").start()
        except Exception as exc:
            try:
                audit_store.fail_run(run_id, safe_error_message(exc), utc_now_iso())
            except Exception:
                pass
            verification_slots.release()
            raise

        yield encode_event("run", {"run_id": run_id})
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
        headers={
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "X-Verification-Id": run_id,
        },
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/verify/stream")
def verify_stream(
    request: VerificationRequest,
    http_request: Request,
    user: User = Depends(allow_verification),
) -> StreamingResponse:
    if request.show_browser and not is_direct_loopback(http_request):
        raise HTTPException(status_code=403, detail="Visible browser demos are available only from localhost.")
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
        input_type="url" if request.url.strip() else "text",
        input_text=request.text.strip(),
        article_url=request.url.strip(),
        owner_user_id=user.id,
    )


@app.post("/api/verify/image/stream")
async def verify_image_stream(
    request: Request,
    image: UploadFile = File(...),
    caption: str = Form(default="", max_length=12_000),
    mode: ReviewMode = Form(default="quick"),
    show_browser: bool = Form(default=False),
    user: User = Depends(allow_verification),
) -> StreamingResponse:
    if show_browser and not is_direct_loopback(request):
        raise HTTPException(status_code=403, detail="Visible browser demos are available only from localhost.")
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

    return stream_verification(
        mode=mode,
        show_browser=show_browser,
        runner=run,
        input_type="image",
        input_text=caption.strip(),
        image_name=image.filename or "uploaded-image",
        owner_user_id=user.id,
    )


@app.get("/api/audits")
def list_audits(limit: int = Query(default=50, ge=1, le=100), user: User = Depends(require_user)) -> dict[str, Any]:
    return {"runs": audit_store.list_runs(limit=limit, owner_user_id=user.id)}


@app.get("/api/audits/{run_id}")
def get_audit(run_id: str, user: User = Depends(require_user)) -> dict[str, Any]:
    if len(run_id) != 32 or any(character not in "0123456789abcdef" for character in run_id.lower()):
        raise HTTPException(status_code=404, detail="Audit record not found.")
    record = audit_store.get_run(run_id, owner_user_id=user.id)
    if record is None:
        raise HTTPException(status_code=404, detail="Audit record not found.")
    return record


frontend_dist = PROJECT_ROOT / "frontend" / "dist"
if frontend_dist.exists():
    @app.get("/{full_path:path}", include_in_schema=False)
    def frontend_app(full_path: str) -> FileResponse:
        resolved_dist = frontend_dist.resolve()
        requested_file = (resolved_dist / full_path).resolve()
        if resolved_dist in requested_file.parents and requested_file.is_file():
            return FileResponse(requested_file)
        return FileResponse(resolved_dist / "index.html")
