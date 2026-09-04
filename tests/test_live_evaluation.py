import json

from scripts.live_evaluation import (
    CASES,
    EvaluationCase,
    build_summary,
    evaluate_report,
    save_results,
)


def test_live_evaluation_requires_two_models_for_a_firm_verdict():
    case = EvaluationCase(
        name="firm_true",
        claim="A supported claim.",
        accepted_verdicts=("True",),
        category="true",
        language="en",
    )
    report = {
        "final_verdict": "True",
        "truth_score": 95,
        "confidence_score": 90,
        "all_evidence": [{"root_domain": "a.example"}, {"root_domain": "b.example"}],
        "verifier_outputs": [{"model_id": "model-a", "verdict": "true"}],
        "gonka_trace": [],
        "limitations": [],
        "source_credibility_assessment": {"independent_source_count": 2},
    }

    result = evaluate_report(case, report, elapsed_seconds=1.0, repetition=1)

    assert result["passed"] is False
    assert result["checks"]["verifier_quorum"] is False
    assert "fewer than 2 verifier models succeeded" in result["failure_reasons"]


def test_live_evaluation_passes_only_when_verdict_and_evidence_quality_pass():
    case = EvaluationCase(
        name="firm_false",
        claim="A contradicted claim.",
        accepted_verdicts=("False", "Mostly False"),
        category="false",
        language="en",
    )
    report = {
        "final_verdict": "False",
        "truth_score": 4,
        "confidence_score": 88,
        "all_evidence": [
            {"evidence_id": "E1", "root_domain": "official.example"},
            {"evidence_id": "E2", "root_domain": "news.example"},
        ],
        "verifier_outputs": [
            {"model_id": "model-a", "verdict": "false"},
            {"model_id": "model-b", "verdict": "mostly_false"},
        ],
        "gonka_trace": [],
        "limitations": [],
        "source_credibility_assessment": {"independent_source_count": 2},
    }

    result = evaluate_report(case, report, elapsed_seconds=1.0, repetition=1)

    assert result["passed"] is True
    assert all(result["checks"].values())
    assert result["failure_reasons"] == []


def test_live_evaluation_summary_reports_repeat_stability():
    results = [
        {
            "case": "stable",
            "passed": True,
            "verdict": "True",
            "elapsed_seconds": 10.0,
            "checks": {"verdict": True},
        },
        {
            "case": "stable",
            "passed": True,
            "verdict": "True",
            "elapsed_seconds": 20.0,
            "checks": {"verdict": True},
        },
        {
            "case": "unstable",
            "passed": True,
            "verdict": "True",
            "elapsed_seconds": 30.0,
            "checks": {"verdict": True},
        },
        {
            "case": "unstable",
            "passed": False,
            "verdict": "False",
            "elapsed_seconds": 40.0,
            "checks": {"verdict": False},
        },
    ]

    summary = build_summary(results)

    assert summary["passed"] == 3
    assert summary["total"] == 4
    assert summary["pass_rate_percent"] == 75.0
    assert summary["average_elapsed_seconds"] == 25.0
    assert summary["stable_cases"] == 1
    assert summary["evaluated_cases"] == 2
    assert summary["stability_rate_percent"] == 50.0
    assert summary["unstable_case_names"] == ["unstable"]


def test_single_run_is_not_reported_as_a_stability_measurement():
    summary = build_summary(
        [
            {
                "case": "only_once",
                "passed": True,
                "verdict": "True",
                "elapsed_seconds": 10.0,
            }
        ]
    )

    assert summary["stable_cases"] == 0
    assert summary["evaluated_cases"] == 0
    assert summary["stability_rate_percent"] == 0.0


def test_live_evaluation_checkpoints_completed_cases(tmp_path):
    path = tmp_path / "live-results.json"
    results = [
        {
            "case": "completed_case",
            "passed": True,
            "verdict": "True",
            "elapsed_seconds": 12.0,
        }
    ]

    payload = save_results(results, path)

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert payload["summary"]["passed"] == 1
    assert stored["results"][0]["case"] == "completed_case"


def test_live_evaluation_matrix_covers_languages_and_verdict_boundaries():
    assert len(CASES) >= 12
    assert {case.language for case in CASES} >= {"en", "zh"}
    assert {case.category for case in CASES} >= {
        "true",
        "false",
        "misleading",
        "unverified",
    }
    assert len({case.name for case in CASES}) == len(CASES)
    for case in CASES:
        if case.category == "unverified":
            assert case.min_evidence == 0
            assert case.min_independent_sources == 0
            assert case.min_successful_models == 0
