from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import ConfigError, load_config
from backend.services.gonka_client import GonkaCallFailed, GonkaClient, GonkaClientError, redact_secrets


TEST_PROMPT = "Reply with exactly: GONKA_TEST_OK"
RESULTS_PATH = ROOT / "test_results.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Live Gonka Router smoke test.")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--list", action="store_true", help="List all models available to the account.")
    actions.add_argument("--model", help="Test one exact model ID.")
    actions.add_argument("--test-first", type=int, metavar="N", help="Test the first N listed models.")
    actions.add_argument("--all", action="store_true", help="Test all listed models.")
    actions.add_argument("--configured", action="store_true", help="Test the distinct models selected in .env.")
    parser.add_argument("--output", type=Path, default=RESULTS_PATH, help="Path for the redacted JSON results.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(ROOT / ".env")
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if not config.gonka_api_key:
        print("Configuration error: set GONKA_API_KEY in .env before live smoke testing.", file=sys.stderr)
        return 2

    client = GonkaClient(config)
    secrets = [config.gonka_api_key, config.tavily_api_key]

    if args.list:
        return list_models(client, secrets)

    try:
        if args.configured:
            selected = list(dict.fromkeys(model for model in (
                config.claim_model, config.gonka_verify_model_1, config.gonka_verify_model_2,
                config.judge_model, config.gonka_fallback_model,
            ) if model))
            mode = "configured"
        else:
            selected, mode = select_models(client, args)
    except Exception as exc:
        print(f"Could not select models: {redact_secrets(str(exc), secrets)}", file=sys.stderr)
        return 1

    if args.all and len(selected) > 10:
        print(f"Warning: --all will test {len(selected)} models and may consume credits or hit rate limits.")

    results = []
    for model_id in selected:
        print(f"Testing {model_id} ...", flush=True)
        results.append(run_smoke_check(client, model_id, secrets))
    print_results_table(results)
    save_results(config.gonka_base_url, mode, results, secrets, path=args.output)
    print(f"\nSaved safe report to {args.output}")
    return 0 if all(item["success"] for item in results) else 1


def list_models(client: GonkaClient, secrets: list[str]) -> int:
    try:
        models = client.list_models()
    except GonkaClientError as exc:
        print(f"Could not list models: {redact_secrets(str(exc), secrets)}", file=sys.stderr)
        if exc.status_code is not None:
            print(f"HTTP status: {exc.status_code}", file=sys.stderr)
        if exc.response_body:
            print(f"Response body: {redact_secrets(exc.response_body, secrets)}", file=sys.stderr)
        return 1
    if not models:
        print("Empty model list: Gonka returned no models for this account.", file=sys.stderr)
        return 1
    print("Available Gonka model IDs:")
    for model in models:
        print(model)
    return 0


def select_models(client: GonkaClient, args: argparse.Namespace) -> tuple[list[str], str]:
    if args.model:
        return [args.model], f"model:{args.model}"
    models = client.list_models()
    if not models:
        raise ValueError("Gonka returned an empty model list.")
    if args.test_first is not None:
        if args.test_first <= 0:
            raise ValueError("--test-first must be a positive integer.")
        return models[: args.test_first], f"test-first:{args.test_first}"
    if args.all:
        return models, "all"
    raise ValueError("Choose a smoke-test mode.")


def run_smoke_check(client: GonkaClient, model_id: str, secrets: list[str]) -> dict[str, Any]:
    try:
        result = client.chat(
            step_name="smoke_test",
            model_id=model_id,
            messages=[{"role": "user", "content": TEST_PROMPT}],
            max_tokens=1024,
        )
        trace = result.trace.model_dump()
        exact_reply = result.text.strip() == "GONKA_TEST_OK"
        return {
            "timestamp_utc": utc_now_iso(),
            "requested_model_id": model_id,
            "returned_model_id": trace.get("returned_model_id"),
            "success": True,
            "status": "ok",
            "http_status": None,
            "response_text": redact_secrets(result.text, secrets),
            "response_body_id": trace.get("response_body_id"),
            "request_id": trace.get("request_id"),
            "trace_id": trace.get("trace_id"),
            "latency_ms": trace.get("latency_ms"),
            "token_usage": trace.get("token_usage"),
            "error_type": None,
            "safe_error_message": None,
            "warning": smoke_warning(trace, exact_reply),
        }
    except GonkaCallFailed as exc:
        trace = exc.trace.model_dump()
        return {
            "timestamp_utc": utc_now_iso(),
            "requested_model_id": model_id,
            "returned_model_id": None,
            "success": False,
            "status": "failed",
            "http_status": exc.error.status_code,
            "response_text": None,
            "response_body_id": None,
            "request_id": trace.get("request_id"),
            "trace_id": trace.get("trace_id"),
            "latency_ms": trace.get("latency_ms"),
            "token_usage": None,
            "error_type": exc.error.error_type,
            "safe_error_message": redact_secrets(str(exc), secrets),
            "warning": None,
        }


def smoke_warning(trace: dict[str, Any], exact_reply: bool) -> str | None:
    warnings = []
    if not trace.get("request_id") and not trace.get("trace_id"):
        warnings.append("No request or trace ID returned.")
    if not exact_reply:
        warnings.append("Model did not reply with exactly GONKA_TEST_OK.")
    return " ".join(warnings) if warnings else None


def print_results_table(results: list[dict[str, Any]]) -> None:
    headers = ["Model", "Status", "Response", "Response ID", "Request/Trace ID", "Latency"]
    rows = []
    for item in results:
        rows.append(
            [
                item["requested_model_id"],
                "OK" if item["success"] else "FAILED",
                one_line(item.get("response_text") or item.get("safe_error_message") or ""),
                item.get("response_body_id") or "",
                item.get("request_id") or item.get("trace_id") or "(none returned)",
                f"{item.get('latency_ms')} ms",
            ]
        )
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) if rows else len(headers[i]) for i in range(6)]
    widths = [min(width, 44) for width in widths]
    print(format_row(headers, widths))
    print(format_row(["-" * width for width in widths], widths))
    for row in rows:
        print(format_row(row, widths))


def save_results(base_url: str, mode: str, results: list[dict[str, Any]], secrets: list[str], *, path: Path = RESULTS_PATH) -> None:
    payload = {
        "generated_at_utc": utc_now_iso(),
        "base_url": base_url,
        "mode": mode,
        "result_count": len(results),
        "results": results,
    }
    text = redact_secrets(json.dumps(payload, indent=2, ensure_ascii=True), secrets)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def format_row(values: list[str], widths: list[int]) -> str:
    return " | ".join(truncate(str(values[i]), widths[i]).ljust(widths[i]) for i in range(len(values)))


def truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    return value[: max(width - 3, 0)] + "..."


def one_line(value: str) -> str:
    return " ".join(str(value).split())


if __name__ == "__main__":
    raise SystemExit(main())
