from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from openai import AsyncOpenAI, OpenAI

from config import AppConfig
from schemas.models import GonkaTraceRecord


TRACE_HEADER_CANDIDATES = (
    "x-trace-id",
    "trace-id",
    "x-gonka-trace-id",
    "x-request-trace-id",
)


class GonkaClientError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_body: str | None = None,
        error_type: str = "GonkaClientError",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body
        self.error_type = error_type


@dataclass(frozen=True)
class GonkaTextResult:
    text: str
    trace: GonkaTraceRecord


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_prompt(name: str) -> str:
    path = Path(__file__).resolve().parents[1] / "prompts" / name
    return path.read_text(encoding="utf-8")


class GonkaClient:
    def __init__(self, config: AppConfig, timeout: float | None = None, max_retries: int = 0) -> None:
        self.config = config
        self._secrets = [config.gonka_api_key, config.tavily_api_key]
        self._timeout_seconds = timeout if timeout is not None else config.gonka_timeout_seconds
        self._max_retries = max_retries
        self.client = OpenAI(
            api_key=config.gonka_api_key,
            base_url=config.gonka_base_url,
            timeout=self._http_timeout(),
            max_retries=max_retries,
        )

    def _http_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            self._timeout_seconds,
            connect=min(20.0, self._timeout_seconds),
        )

    def list_models(self) -> list[str]:
        try:
            raw_response = self.client.models.with_raw_response.list()
            parsed = raw_response.parse()
        except Exception as exc:
            raise convert_exception(exc, secrets=self._secrets) from exc

        data = get_value(parsed, "data", [])
        model_ids = []
        for item in data or []:
            model_id = get_value(item, "id")
            if isinstance(model_id, str) and model_id.strip():
                model_ids.append(model_id)
        return model_ids

    def chat(
        self,
        *,
        step_name: str,
        model_id: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> GonkaTextResult:
        timestamp = utc_now_iso()
        start = time.perf_counter()
        try:
            headers, parsed = asyncio.run(
                self._chat_with_total_deadline(
                    model_id=model_id,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            )
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            request_id, trace_id = extract_request_trace_ids(headers)
            text = extract_response_text(parsed)
            trace = GonkaTraceRecord(
                step_name=step_name,
                requested_model_id=model_id,
                returned_model_id=get_value(parsed, "model"),
                response_body_id=get_value(parsed, "id"),
                request_id=request_id,
                trace_id=trace_id,
                timestamp_utc=timestamp,
                latency_ms=latency_ms,
                token_usage=usage_to_dict(get_value(parsed, "usage")),
                success=True,
            )
            return GonkaTextResult(text=redact_secrets(text, self._secrets), trace=trace)
        except Exception as exc:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            headers = extract_exception_headers(exc)
            request_id, trace_id = extract_request_trace_ids(headers)
            error = convert_exception(exc, secrets=self._secrets)
            trace = GonkaTraceRecord(
                step_name=step_name,
                requested_model_id=model_id,
                returned_model_id=None,
                response_body_id=None,
                request_id=request_id,
                trace_id=trace_id,
                timestamp_utc=timestamp,
                latency_ms=latency_ms,
                token_usage=None,
                success=False,
                error_type=error.error_type,
                safe_error_message=redact_secrets(str(error), self._secrets),
            )
            raise GonkaCallFailed(error, trace) from exc

    async def _chat_with_total_deadline(
        self,
        *,
        model_id: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
    ) -> tuple[dict[str, str], Any]:
        async with asyncio.timeout(self._timeout_seconds):
            async with AsyncOpenAI(
                api_key=self.config.gonka_api_key,
                base_url=self.config.gonka_base_url,
                timeout=self._http_timeout(),
                max_retries=self._max_retries,
            ) as client:
                raw_response = await client.chat.completions.with_raw_response.create(
                    model=model_id,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            headers = normalize_headers(getattr(raw_response, "headers", {}))
            parsed = raw_response.parse()
            return headers, parsed

    def chat_json(
        self,
        *,
        step_name: str,
        model_id: str,
        prompt: str,
        user_payload: dict[str, Any],
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> GonkaTextResult:
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=True, indent=2),
            },
        ]
        return self.chat(
            step_name=step_name,
            model_id=model_id,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def describe_image(
        self,
        *,
        model_id: str,
        image_bytes: bytes,
        mime_type: str,
        caption: str,
        ocr_text: str,
    ) -> GonkaTextResult:
        data_url = "data:{mime};base64,{data}".format(
            mime=mime_type,
            data=base64.b64encode(image_bytes).decode("ascii"),
        )
        prompt = load_prompt("image_context_analyser.txt")
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "caption_or_claim": caption,
                                "ocr_text_from_tesseract": ocr_text,
                            },
                            ensure_ascii=True,
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ]
        return self.chat(
            step_name="image_context_analysis",
            model_id=model_id,
            messages=messages,
            max_tokens=1024,
        )


class GonkaCallFailed(Exception):
    def __init__(self, error: GonkaClientError, trace: GonkaTraceRecord) -> None:
        super().__init__(str(error))
        self.error = error
        self.trace = trace


def parse_json_object(text: str) -> dict[str, Any]:
    clean = strip_private_reasoning(text).strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean)
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", clean, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def extract_response_text(parsed: Any) -> str:
    choices = get_value(parsed, "choices", [])
    if not choices:
        return ""
    message = get_value(choices[0], "message", {})
    content = get_value(message, "content", "")
    if isinstance(content, str):
        return strip_private_reasoning(content)
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            text = get_value(part, "text")
            if isinstance(text, str):
                chunks.append(text)
        return strip_private_reasoning("".join(chunks))
    return strip_private_reasoning(str(content)) if content is not None else ""


def strip_private_reasoning(text: str) -> str:
    clean = re.sub(r"(?is)<think>.*?</think>", "", text or "")
    if clean.strip().lower().startswith("<think>"):
        return ""
    return clean.strip()


def usage_to_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    if isinstance(usage, dict):
        return dict(usage)
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if hasattr(usage, "dict"):
        return usage.dict()
    result: dict[str, Any] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, key, None)
        if value is not None:
            result[key] = value
    return result or None


def normalize_headers(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    items = headers.items() if hasattr(headers, "items") else headers
    return {str(key).lower(): str(value) for key, value in items}


def extract_request_trace_ids(headers: Any) -> tuple[str | None, str | None]:
    normalized = normalize_headers(headers)
    request_id = normalized.get("x-request-id") or normalized.get("request-id")
    trace_id = None
    for key in TRACE_HEADER_CANDIDATES:
        if normalized.get(key):
            trace_id = normalized[key]
            break
    if trace_id is None:
        for key, value in normalized.items():
            if "trace" in key:
                trace_id = value
                break
    return request_id, trace_id


def redact_secrets(text: Any, secrets: list[str] | None = None) -> str:
    if text is None:
        return ""
    redacted = str(text)
    for secret in secrets or []:
        if secret and len(secret) >= 4:
            redacted = redacted.replace(secret, "[REDACTED]")
    patterns = [
        (r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+", r"\1[REDACTED]"),
        (r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]{12,}", r"\1[REDACTED]"),
        (r"(?i)(GONKA_API_KEY\s*=\s*)[^\s]+", r"\1[REDACTED]"),
        (r"(?i)(api[_-]?key['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._\-+/=]{8,}", r"\1[REDACTED]"),
    ]
    for pattern, replacement in patterns:
        redacted = re.sub(pattern, replacement, redacted)
    return redacted


def convert_exception(exc: Exception, secrets: list[str] | None = None) -> GonkaClientError:
    if isinstance(exc, GonkaClientError):
        return exc
    status_code = getattr(exc, "status_code", None)
    response_body = redact_secrets(get_error_response_body(exc) or "", secrets)
    message = redact_secrets(str(exc), secrets)
    helper = status_help(status_code)
    if is_timeout(exc):
        helper = "Timeout while contacting Gonka Router."
    elif looks_like_json_error(exc):
        helper = "Invalid JSON response from Gonka Router."
    full_message = f"{helper} {message}".strip() if helper else (message or exc.__class__.__name__)
    return GonkaClientError(
        full_message,
        status_code=status_code,
        response_body=response_body,
        error_type=exc.__class__.__name__,
    )


def status_help(status_code: int | None) -> str:
    if status_code == 400:
        return "HTTP 400: bad request, possibly an unknown model ID."
    if status_code == 401:
        return "HTTP 401: invalid API key or unauthorized Gonka account."
    if status_code == 404:
        return "HTTP 404: unsupported endpoint or route."
    if status_code == 429:
        return "HTTP 429: rate limit reached."
    return ""


def is_timeout(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    return "timeout" in name or "timed out" in str(exc).lower()


def looks_like_json_error(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    return "json" in name or "invalid json" in str(exc).lower()


def get_error_response_body(exc: Exception) -> str | None:
    response = getattr(exc, "response", None)
    if response is None:
        return None
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    try:
        return json.dumps(response.json(), ensure_ascii=True)
    except Exception:
        return None


def extract_exception_headers(exc: Exception) -> dict[str, str]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    return normalize_headers(headers)


def get_value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)
