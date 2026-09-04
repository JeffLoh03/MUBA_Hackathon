from scripts.gonka_smoke_test import print_results_table


def test_smoke_table_prints_model_compliance_warning(capsys):
    print_results_table(
        [
            {
                "requested_model_id": "model-a",
                "success": True,
                "response_text": "GONKA_TEST_OKGONKA_TEST_OK",
                "safe_error_message": None,
                "response_body_id": "response-1",
                "request_id": "request-1",
                "trace_id": None,
                "latency_ms": 12.5,
                "warning": "Model did not reply with exactly GONKA_TEST_OK.",
            }
        ]
    )

    output = capsys.readouterr().out
    assert "Warnings:" in output
    assert "model-a: Model did not reply with exactly GONKA_TEST_OK." in output
