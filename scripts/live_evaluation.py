from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
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
    category: str = "general"
    language: str = "en"
    min_evidence: int = 2
    min_independent_sources: int = 2
    min_successful_models: int = 2


CASES = (
    EvaluationCase(
        "chinese_true_dart",
        "美国国家航空航天局证实，DART任务改变了小行星的轨道。",
        ("True", "Mostly True"),
        category="true",
        language="zh",
    ),
    EvaluationCase(
        "chinese_false_dart",
        "美国国家航空航天局证实，DART任务让小行星对地球构成威胁。",
        ("False", "Mostly False"),
        category="false",
        language="zh",
    ),
    EvaluationCase(
        "who_true_emergency_end",
        "The World Health Organization ended the COVID-19 global health emergency on 5 May 2023.",
        ("True", "Mostly True"),
        category="true",
        language="en",
    ),
    EvaluationCase(
        "who_false_microchips",
        "The World Health Organization confirmed that COVID-19 vaccines contain tracking microchips.",
        ("False", "Mostly False"),
        category="false",
        language="en",
    ),
    EvaluationCase(
        "malaya_true_independence",
        "The Federation of Malaya became independent on 31 August 1957.",
        ("True", "Mostly True"),
        category="true",
        language="en",
    ),
    EvaluationCase(
        "malaysia_false_currency",
        "Malaysia's official currency is the US dollar.",
        ("False", "Mostly False"),
        category="false",
        language="en",
    ),
    EvaluationCase(
        "invented_event_unverified",
        "The town of Zorbax opened an 800-kilometre glass railway yesterday.",
        ("Unverified",),
        category="unverified",
        language="en",
        min_evidence=0,
        min_independent_sources=0,
        min_successful_models=0,
    ),
    EvaluationCase(
        "chinese_true_chandrayaan",
        "印度的月船3号于2023年8月23日在月球南极附近成功软着陆。",
        ("True", "Mostly True"),
        category="true",
        language="zh",
    ),
    EvaluationCase(
        "india_false_first_moon_landing",
        "India was the first country in history to achieve a soft landing on the Moon.",
        ("False", "Mostly False"),
        category="false",
        language="en",
    ),
    EvaluationCase(
        "chinese_false_great_wall_moon",
        "中国长城是唯一能从月球上用肉眼看见的人造建筑。",
        ("False", "Mostly False"),
        category="false",
        language="zh",
    ),
    EvaluationCase(
        "who_misleading_pandemic_end",
        "On 5 May 2023, WHO announced that the COVID-19 pandemic had ended completely.",
        ("Misleading", "Mostly False", "False"),
        category="misleading",
        language="en",
    ),
    EvaluationCase(
        "malaysia_misleading_independence_date",
        "Malaysia gained independence from Britain on 16 September 1963.",
        ("Misleading", "Mostly False", "False"),
        category="misleading",
        language="en",
    ),
    EvaluationCase(
        "invented_study_unverified",
        "The Zorbax Institute published a 2026 clinical trial proving that moonlight cures diabetes.",
        ("Unverified",),
        category="unverified",
        language="en",
        min_evidence=0,
        min_independent_sources=0,
        min_successful_models=0,
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run real end-to-end fact-check evaluations.")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
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

    result = evaluate_report(case, report, elapsed_seconds, repetition)
    print_result(result)
    return result


def evaluate_report(
    case: EvaluationCase,
    report: dict[str, Any],
    elapsed_seconds: float,
    repetition: int,
) -> dict[str, Any]:
    verifier_traces = [
        trace
        for trace in report["gonka_trace"]
        if trace["step_name"].startswith(("verifier_1", "verifier_2", "verifier_fallback"))
    ]
    verdict = report["final_verdict"]
    evidence_count = len(report["all_evidence"])
    successful_models = [item.get("model_id", "") for item in report["verifier_outputs"]]
    independent_source_count = report.get("source_credibility_assessment", {}).get(
        "independent_source_count", 0
    )
    checks = {
        "verdict": verdict in case.accepted_verdicts,
        "evidence_count": evidence_count >= case.min_evidence,
        "independent_sources": independent_source_count >= case.min_independent_sources,
        "verifier_quorum": len(successful_models) >= case.min_successful_models,
    }
    failure_reasons = []
    if not checks["verdict"]:
        failure_reasons.append(
            f"verdict {verdict!r} was not one of {', '.join(case.accepted_verdicts)}"
        )
    if not checks["evidence_count"]:
        failure_reasons.append(f"fewer than {case.min_evidence} evidence items were retained")
    if not checks["independent_sources"]:
        failure_reasons.append(
            f"fewer than {case.min_independent_sources} independent sources were retained"
        )
    if not checks["verifier_quorum"]:
        failure_reasons.append(
            f"fewer than {case.min_successful_models} verifier models succeeded"
        )
    result = {
        "case": case.name,
        "category": case.category,
        "language": case.language,
        "repetition": repetition,
        "passed": all(checks.values()),
        "checks": checks,
        "failure_reasons": failure_reasons,
        "accepted_verdicts": list(case.accepted_verdicts),
        "verdict": verdict,
        "truth_score": report["truth_score"],
        "confidence_score": report["confidence_score"],
        "evidence_count": evidence_count,
        "independent_source_count": independent_source_count,
        "successful_models": successful_models,
        "model_outputs": [
            {
                "model": item.get("model_id", ""),
                "verdict": item.get("verdict"),
                "support_score": item.get("support_score"),
                "confidence": item.get("confidence"),
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
                "id": item.get("evidence_id", ""),
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "domain": item.get("root_domain", ""),
                "source_type": item.get("source_type", ""),
                "source_quality": item.get("source_quality", ""),
                "excerpt": item.get("excerpt", ""),
            }
            for item in report["all_evidence"]
        ],
    }
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


def build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(bool(result.get("passed")) for result in results)
    elapsed_values = [
        float(result["elapsed_seconds"])
        for result in results
        if isinstance(result.get("elapsed_seconds"), (int, float))
    ]
    verdicts_by_case: dict[str, list[str]] = {}
    for result in results:
        case_name = str(result.get("case", "unknown"))
        verdict = str(result.get("verdict", "API ERROR"))
        verdicts_by_case.setdefault(case_name, []).append(verdict)

    repeated_verdicts = {
        name: verdicts for name, verdicts in verdicts_by_case.items() if len(verdicts) >= 2
    }
    unstable_case_names = sorted(
        name for name, verdicts in repeated_verdicts.items() if len(set(verdicts)) > 1
    )
    evaluated_cases = len(repeated_verdicts)
    stable_cases = evaluated_cases - len(unstable_case_names)
    return {
        "passed": passed,
        "total": total,
        "pass_rate_percent": round((passed / total) * 100, 2) if total else 0.0,
        "average_elapsed_seconds": (
            round(sum(elapsed_values) / len(elapsed_values), 2) if elapsed_values else 0.0
        ),
        "stable_cases": stable_cases,
        "evaluated_cases": evaluated_cases,
        "stability_rate_percent": (
            round((stable_cases / evaluated_cases) * 100, 2) if evaluated_cases else 0.0
        ),
        "unstable_case_names": unstable_case_names,
    }


def save_results(results: list[dict[str, Any]], path: Path = RESULTS_PATH) -> dict[str, Any]:
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "summary": build_summary(results),
        "results": results,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return payload


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
            for repetition in range(1, args.repeat + 1):
                for case in selected_cases:
                    results.append(run_case(client, args.api_url, case, repetition))
                    save_results(results)
    except Exception as exc:
        print(f"Live evaluation stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    payload = save_results(results)
    print(f"Saved secret-safe results to {RESULTS_PATH}")
    print(f"Passed {payload['summary']['passed']}/{payload['summary']['total']} cases.")
    return 0 if payload["summary"]["passed"] == payload["summary"]["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
