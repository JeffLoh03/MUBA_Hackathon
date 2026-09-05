from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


DEFAULT_API_URL = "http://127.0.0.1:8000"
RESULTS_PATH = Path(__file__).resolve().parents[1] / "live_evaluation_results.json"


@dataclass(frozen=True)
class EvaluationCase:
    name: str
    claim: str
    accepted_verdicts: tuple[str, ...]


CASES = (
    EvaluationCase(
        "chinese_true_dart",
        "美国国家航空航天局证实，DART任务改变了小行星的轨道。",
        ("True", "Mostly True"),
    ),
    EvaluationCase(
        "chinese_false_dart",
        "美国国家航空航天局证实，DART任务让小行星对地球构成威胁。",
        ("False", "Mostly False"),
    ),
    EvaluationCase(
        "who_true_emergency_end",
        "The World Health Organization ended the COVID-19 global health emergency on 5 May 2023.",
        ("True", "Mostly True"),
    ),
    EvaluationCase(
        "who_false_microchips",
        "The World Health Organization confirmed that COVID-19 vaccines contain tracking microchips.",
        ("False", "Mostly False"),
    ),
    EvaluationCase(
        "malaya_true_independence",
        "The Federation of Malaya became independent on 31 August 1957.",
        ("True", "Mostly True"),
    ),
    EvaluationCase(
        "malaysia_false_currency",
        "Malaysia's official currency is the US dollar.",
        ("False", "Mostly False"),
    ),
    EvaluationCase(
        "invented_event_unverified",
        "The town of Zorbax opened an 800-kilometre glass railway yesterday.",
        ("Unverified",),
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run real end-to-end fact-check evaluations.")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--email", required=True, help="Existing desk account email; password is prompted securely.")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument(
        "--case",
        action="append",
        choices=[case.name for case in CASES],
        help="Run only the named case. Repeat this option to select multiple cases.",
    )
    return parser.parse_args()


def check_health(client: httpx.Client, api_url: str) -> None:
    response = client.get(f"{api_url.rstrip('/')}/api/health")
    response.raise_for_status()
    if response.json().get("status") != "ok":
        raise RuntimeError("The local verification API health check did not return ok.")


def run_case(
    client: httpx.Client,
    api_url: str,
    case: EvaluationCase,
    repetition: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    report = None
    api_error = None
    print(f"Running {case.name} (repeat {repetition})...", flush=True)
    with client.stream(
        "POST",
        f"{api_url.rstrip('/')}/api/verify/stream",
        json={"text": case.claim, "url": "", "mode": "quick", "show_browser": False},
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.strip():
                continue
            message = json.loads(line)
            if message["type"] == "report":
                report = message["data"]["report"]
            elif message["type"] == "error":
                api_error = message["data"]

    elapsed_seconds = round(time.perf_counter() - started, 2)
    if report is None:
        result = {
            "case": case.name,
            "repetition": repetition,
            "passed": False,
            "accepted_verdicts": list(case.accepted_verdicts),
            "elapsed_seconds": elapsed_seconds,
            "api_error": api_error,
        }
        print_result(result)
        return result

    verifier_traces = [
        trace
        for trace in report["gonka_trace"]
        if trace["step_name"].startswith(("verifier_1", "verifier_2", "verifier_fallback"))
    ]
    verdict = report["final_verdict"]
    result = {
        "case": case.name,
        "repetition": repetition,
        "passed": verdict in case.accepted_verdicts,
        "accepted_verdicts": list(case.accepted_verdicts),
        "verdict": verdict,
        "truth_score": report["truth_score"],
        "confidence_score": report["confidence_score"],
        "evidence_count": len(report["all_evidence"]),
        "successful_models": [item.get("model_id", "") for item in report["verifier_outputs"]],
        "model_outputs": [
            {
                "model": item.get("model_id", ""),
                "verdict": item["verdict"],
                "support_score": item["support_score"],
                "confidence": item["confidence"],
            }
            for item in report["verifier_outputs"]
        ],
        "failed_models": [
            trace["requested_model_id"] for trace in verifier_traces if not trace["success"]
        ],
        "failed_model_errors": [
            {
                "model": trace["requested_model_id"],
                "error_type": trace.get("error_type"),
                "safe_error_message": trace.get("safe_error_message"),
            }
            for trace in verifier_traces
            if not trace["success"]
        ],
        "request_id_count": sum(
            bool(trace.get("request_id") or trace.get("trace_id"))
            for trace in verifier_traces
            if trace["success"]
        ),
        "elapsed_seconds": elapsed_seconds,
        "limitations": report["limitations"],
        "source_credibility": report["source_credibility_assessment"],
        "evidence": [
            {
                "id": item["evidence_id"],
                "title": item["title"],
                "url": item["url"],
                "domain": item["root_domain"],
                "source_type": item["source_type"],
                "source_quality": item["source_quality"],
                "excerpt": item["excerpt"],
            }
            for item in report["all_evidence"]
        ],
    }
    print_result(result)
    return result


def print_result(result: dict[str, Any]) -> None:
    status = "PASS" if result["passed"] else "FAIL"
    verdict = result.get("verdict", "API ERROR")
    print(
        f"{status:4} | {result['case']:<30} | {verdict:<14} | "
        f"evidence={result.get('evidence_count', 0):<2} | "
        f"models={len(result.get('successful_models', []))} | "
        f"{result['elapsed_seconds']:.2f}s",
        flush=True,
    )


def main() -> int:
    args = parse_args()
    if args.repeat < 1 or args.repeat > 10:
        print("--repeat must be between 1 and 10.", file=sys.stderr)
        return 2

    results = []
    selected_cases = tuple(case for case in CASES if not args.case or case.name in args.case)
    try:
        with httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
            check_health(client, args.api_url)
            response = client.post(f"{args.api_url.rstrip('/')}/api/auth/login", json={"email": args.email, "password": getpass.getpass("Desk password: ")})
            response.raise_for_status()
            for repetition in range(1, args.repeat + 1):
                for case in selected_cases:
                    results.append(run_case(client, args.api_url, case, repetition))
    except Exception as exc:
        print(f"Live evaluation stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    payload = {
        "summary": {
            "passed": sum(result["passed"] for result in results),
            "total": len(results),
        },
        "results": results,
    }
    RESULTS_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    print(f"Saved secret-safe results to {RESULTS_PATH}")
    print(f"Passed {payload['summary']['passed']}/{payload['summary']['total']} cases.")
    return 0 if payload["summary"]["passed"] == payload["summary"]["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
