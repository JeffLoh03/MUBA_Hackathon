import json
from types import SimpleNamespace

import pytest

from backend import api
from backend.schemas.models import GonkaTraceRecord
from test_multi_claim import CLAIM_TRUE, make_pipeline


def setup_review(monkeypatch, *, enabled=True, gaps=True, broken=False, search_failure=False):
    pipeline, client, search = make_pipeline([CLAIM_TRUE])
    pipeline.enable_deep_review = enabled
    original_chat = client.chat_json

    def chat(**kwargs):
        if kwargs['step_name'].startswith('evidence_gap_review'):
            client.calls.append((kwargs['step_name'], kwargs['user_payload']))
            return SimpleNamespace(text='{}' if broken else json.dumps({
                'summary': 'Check an independent astronomical source.',
                'gaps': ['Independent confirmation'] if gaps else [],
                'follow_up_queries': ['independent astronomical record'] if gaps else [],
            }), trace=GonkaTraceRecord(step_name=kwargs['step_name'], requested_model_id='claim-model',
                request_id='gap-request', timestamp_utc='2026-09-05T00:00:00Z', latency_ms=1, success=True))
        return original_chat(**kwargs)

    monkeypatch.setattr(client, 'chat_json', chat)
    original_search = search.search_many

    def search_many(queries, max_results_per_query):
        if queries == ['independent astronomical record']:
            if search_failure:
                raise RuntimeError('provider-secret-must-not-leak')
            return original_search([CLAIM_TRUE], max_results_per_query)
        return original_search(queries, max_results_per_query)

    monkeypatch.setattr(search, 'search_many', search_many)
    processor = pipeline.evidence_processor
    original_evidence = processor.build_evidence
    calls = []

    def build(results):
        calls.append(1)
        items = original_evidence(results)
        if len(calls) > 1:
            items[0] = items[0].model_copy(update={'url': 'https://second.gov/record',
                'root_domain': 'second.gov', 'excerpt': CLAIM_TRUE + ' Independent observations and historical catalogue measurements substantiate this record with a separately published method.'})
        return items

    monkeypatch.setattr(processor, 'build_evidence', build)
    return pipeline, client, search


def test_professional_follow_up_reaches_both_verifiers_and_is_saved(monkeypatch):
    pipeline, client, search = setup_review(monkeypatch)
    report = pipeline.verify(text=CLAIM_TRUE)
    assert report.deep_review.status == 'completed'
    assert report.deep_review.additional_source_count == 1
    assert len(search.calls) == 2
    assert any(trace.request_id == 'gap-request' for trace in report.gonka_trace)
    for step, payload in client.calls:
        if step.startswith('verifier'):
            assert any(item['url'] == 'https://second.gov/record' for item in payload['evidence'])
            assert len({item['evidence_id'] for item in payload['evidence']}) == len(payload['evidence'])
    assert report.model_dump()['deep_review']['follow_up_queries']


def test_quick_does_not_run_extra_research(monkeypatch):
    pipeline, client, search = setup_review(monkeypatch, enabled=False)
    report = pipeline.verify(text=CLAIM_TRUE)
    assert report.deep_review is None
    assert len(search.calls) == 1
    assert not any(step.startswith('evidence_gap_review') for step, _ in client.calls)


@pytest.mark.parametrize('settings,status', [({'broken': True}, 'failed'), ({'search_failure': True}, 'partial'), ({'gaps': False}, 'completed')])
def test_optional_research_failure_or_sufficient_coverage_preserves_initial_review(monkeypatch, settings, status):
    pipeline, client, _ = setup_review(monkeypatch, **settings)
    report = pipeline.verify(text=CLAIM_TRUE)
    assert report.deep_review.status == status
    assert report.deep_review.additional_source_count == 0
    assert len(report.verifier_outputs) == 2
    assert 'provider-secret-must-not-leak' not in report.model_dump_json()


def test_api_selects_deep_research_only_for_professional():
    pipeline, client, _ = make_pipeline([CLAIM_TRUE])
    for mode, enabled, limit in [('quick', False, 5), ('professional', True, 12)]:
        selected = api.build_text_pipeline(config=pipeline.config, client=client, mode=mode, emit=lambda *args: None, browser_demo=None)
        assert selected.enable_deep_review is enabled
        assert selected.evidence_processor.max_evidence == limit
