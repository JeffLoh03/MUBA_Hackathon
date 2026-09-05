from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from pydantic import ValidationError

from backend.config import AppConfig
from backend.pipeline.consensus import build_consensus, needs_judge
from backend.schemas.models import (
    ArticleContent,
    ClaimExtraction,
    DeepReview,
    EvidenceGapPlan,
    EvidenceItem,
    FactCheckReport,
    GonkaTraceRecord,
    SearchQueries,
    SourceCredibilityAssessment,
    VerifierOutput,
)
from backend.services.article_extractor import ArticleFetchError, URLSafetyError, extract_article
from backend.services.evidence_processor import (
    EvidenceProcessor,
    normalize_excerpt,
    relabel_evidence,
    remove_near_duplicates,
    split_evidence_by_model_outputs,
)
from backend.services.gonka_client import GonkaCallFailed, GonkaClient, load_prompt, parse_json_object
from backend.services.search_provider import SearchProvider, deterministic_search_queries
from backend.services.source_credibility import assess_source_credibility
from backend.services.source_ranker import classify_source, publisher_from_url, root_domain


ProgressCallback = Callable[[str, dict[str, Any]], None]
NON_ENGLISH_SCRIPT_PATTERN = re.compile(
    r"[\u0370-\u052f\u0590-\u08ff\u0900-\u0fff\u3040-\u30ff"
    r"\u3400-\u9fff\uac00-\ud7af\uf900-\ufaff]"
)


class PipelineConfigError(Exception):
    pass


class PipelineStepFailed(Exception):
    def __init__(self, message: str, traces: list[Any]) -> None:
        super().__init__(message)
        self.traces = traces


class TextFactCheckPipeline:
    def __init__(
        self,
        config: AppConfig,
        gonka_client: GonkaClient,
        search_provider: SearchProvider | None = None,
        evidence_processor: EvidenceProcessor | None = None,
        progress_callback: ProgressCallback | None = None,
        browser_demo: Any | None = None,
        max_results_per_query: int = 4,
        use_ai_search_planning: bool = True,
        use_ai_claim_extraction: bool = True,
        enable_deep_review: bool = False,
    ) -> None:
        self.config = config
        self.gonka_client = gonka_client
        self.search_provider = search_provider or SearchProvider(config)
        self.evidence_processor = evidence_processor or EvidenceProcessor()
        self.progress_callback = progress_callback
        self.browser_demo = browser_demo
        self.max_results_per_query = max(1, max_results_per_query)
        self.use_ai_search_planning = use_ai_search_planning
        self.use_ai_claim_extraction = use_ai_claim_extraction
        self.enable_deep_review = enable_deep_review
        self._claim_context: dict[str, Any] = {}
        self._current_claim_traces: list[GonkaTraceRecord] | None = None

    def verify(self, *, text: str = "", article_url: str = "") -> FactCheckReport:
        self._ensure_ready()
        self._emit(
            "Input received",
            {
                "has_text": bool(text.strip()),
                "has_article_url": bool(article_url.strip()),
            },
        )
        source_text, setup_limitations, source_article = self._prepare_source_text(
            text=text,
            article_url=article_url,
        )
        self._emit(
            "Input prepared",
            {
                "source_text_chars": len(source_text),
                "article_extracted": source_article is not None,
                "limitations": setup_limitations,
            },
        )
        traces = []

        fallback_claim = text.strip() or (source_article.title if source_article else "")
        use_direct_claim = (
            not self.use_ai_claim_extraction
            and bool(text.strip())
            and not article_url.strip()
            and len(text.strip()) <= 500
            and "\n" not in text.strip()
            and not NON_ENGLISH_SCRIPT_PATTERN.search(text)
            and not re.search(r"[.!?;]\s+\S|[。！？；]\S", text.strip())
        )
        self._emit(
            "Claim extraction started",
            {
                "model": None if use_direct_claim else self.config.claim_model,
                "strategy": "direct_text" if use_direct_claim else "ai",
            },
        )
        if use_direct_claim:
            extraction = ClaimExtraction(
                claims=[deterministic_claim_fallback(text, source_text)],
                not_verifiable_reason="",
            )
            extraction_traces = []
            extraction_limitations = []
        else:
            extraction, extraction_traces, extraction_limitations = self._extract_claims(
                source_text,
                fallback_claim=fallback_claim,
            )
        traces.extend(extraction_traces)
        setup_limitations.extend(extraction_limitations)
        unique_claims = unique_extracted_claims(extraction.claims)
        extraction.claims = unique_claims[:3]
        unreviewed_claims = unique_claims[3:]
        if unreviewed_claims:
            setup_limitations.append(
                f"The review is limited to three unique claims; {len(unreviewed_claims)} additional "
                "extracted claim(s) were not reviewed."
            )
        self._emit(
            "Claim extraction completed",
            {
                "claims": extraction.claims,
                "not_verifiable_reason": extraction.not_verifiable_reason,
                "unreviewed_claims": unreviewed_claims,
            },
        )
        if not extraction.claims:
            self._emit("Stopped", {"reason": "Not a verifiable factual claim"})
            return FactCheckReport(
                extracted_claim="",
                extracted_claims=[],
                final_verdict="Not a verifiable factual claim",
                truth_score=50,
                confidence_score=0,
                concise_explanation=extraction.not_verifiable_reason
                or "The input does not contain a concrete factual claim that can be checked.",
                gonka_trace=traces,
                limitations=setup_limitations,
            )

        if len(extraction.claims) == 1:
            return self._verify_claim(
                extraction.claims[0],
                setup_limitations=list(setup_limitations),
                source_article=source_article,
                traces=traces,
            )

        claim_reports: list[FactCheckReport] = []
        for index, claim in enumerate(extraction.claims, start=1):
            self._claim_context = {
                "claim_index": index,
                "claim_count": len(extraction.claims),
                "claim": claim,
            }
            self._current_claim_traces = []
            self._emit("Claim review started", {"claim_count": len(extraction.claims)})
            try:
                claim_report = self._verify_claim(
                    claim,
                    setup_limitations=[],
                    source_article=source_article,
                    traces=[],
                )
            except Exception as exc:
                # Keep completed reviews and continue to the next independent claim.
                # Exception messages can include provider internals; expose only its type.
                self._emit("Claim review failed", {"error_type": type(exc).__name__})
                claim_report = FactCheckReport(
                    extracted_claim=claim,
                    extracted_claims=[claim],
                    final_verdict="Unverified",
                    truth_score=50,
                    confidence_score=0,
                    concise_explanation="This claim could not be reviewed because a processing step failed.",
                    review_status="failed",
                    limitations=[f"Claim review failed ({type(exc).__name__}); no truth verdict was issued."],
                    gonka_trace=list(self._current_claim_traces),
                )
            finally:
                self._current_claim_traces = None
            traces.extend(claim_report.gonka_trace)
            # The parent owns the call ledger. Evidence IDs remain local to each child.
            claim_reports.append(claim_report.model_copy(update={"gonka_trace": []}))
            self._emit(
                "Claim review completed",
                {"final_verdict": claim_report.final_verdict, "review_status": claim_report.review_status},
            )
            self._claim_context = {}

        failed_count = sum(report.review_status == "failed" for report in claim_reports)
        completed_count = len(claim_reports) - failed_count
        setup_limitations.append(
            "Only the listed claims were reviewed, with a maximum of three per submission. "
            "Additional claims in the original input may not have been extracted. "
            "Evidence IDs are scoped to each claim; scores are not combined into an article truth score."
        )
        if failed_count:
            setup_limitations.append(f"{failed_count} claim review(s) failed and remain unverified.")
        return FactCheckReport(
            extracted_claim="Multiple claims reviewed",
            extracted_claims=extraction.claims,
            claim_reports=claim_reports,
            unreviewed_claims=unreviewed_claims,
            review_status="partial" if failed_count or unreviewed_claims else "completed",
            final_verdict="Multiple claims reviewed",
            # Retained for older API consumers; this container has no article-wide score.
            truth_score=50,
            confidence_score=0,
            concise_explanation=(
                f"{completed_count} of {len(claim_reports)} independent claim reviews completed. "
                "Read each claim's verdict, evidence, confidence, and limitations below. "
                "No overall article truth score is calculated."
            ),
            gonka_trace=traces,
            limitations=setup_limitations,
        )

    def _verify_claim(
        self,
        claim: str,
        *,
        setup_limitations: list[str],
        source_article: ArticleContent | None,
        traces: list[GonkaTraceRecord],
    ) -> FactCheckReport:
        self._emit(
            "Search planning started",
            {
                "claim": claim,
                "strategy": "ai" if self.use_ai_search_planning else "deterministic",
                "model": self.config.claim_model if self.use_ai_search_planning else None,
            },
        )
        if self.use_ai_search_planning:
            queries, query_traces, query_limitations = self._plan_searches(claim)
        else:
            queries = deterministic_search_queries(claim)
            query_traces = []
            query_limitations = []
        traces.extend(query_traces)
        self._emit(
            "Search planning completed",
            {
                "queries": queries.as_list(),
                "strategy": "ai" if self.use_ai_search_planning else "deterministic",
                "fallback_limitations": query_limitations,
            },
        )

        self._emit("Web search started", {"query_count": len(queries.as_list())})
        self._preview_browser_searches(queries.as_list())
        search_results = self.search_provider.search_many(
            queries.as_list(),
            max_results_per_query=self.max_results_per_query,
        )
        search_errors = list(getattr(self.search_provider, "last_errors", []))
        search_limitations: list[str] = []
        if search_errors and not search_results:
            search_limitations.append(
                "Web search failed for all planned queries. "
                f"First provider error: {search_errors[0]}"
            )
        elif search_errors:
            search_limitations.append(
                f"{len(search_errors)} planned web search(es) failed; available results were still checked."
            )
        elif not search_results:
            search_limitations.append("Web search returned no results for the planned queries.")
        setup_limitations.extend(search_limitations)
        self._emit(
            "Web search completed",
            {
                "raw_result_count": len(search_results),
                "result_urls": [item.url for item in search_results[:8]],
                "provider_error_count": len(search_errors),
                "provider_errors": search_errors[:2],
            },
        )
        self._preview_browser_urls([item.url for item in search_results[:5]])
        self._emit("Evidence processing started", {"raw_result_count": len(search_results)})
        evidence = self.evidence_processor.build_evidence(search_results)
        if source_article is not None:
            source_evidence = article_to_evidence(source_article)
            evidence = relabel_evidence(remove_near_duplicates([source_evidence, *evidence]))
        evidence = evidence[:getattr(self.evidence_processor, "max_evidence", 12)]
        deep_review = None
        if self.enable_deep_review:
            evidence, deep_review, deep_traces = self._research_gaps(claim, evidence, queries.as_list())
            traces.extend(deep_traces)
            setup_limitations.extend(deep_review.limitations)
        if search_results and not evidence and source_article is None:
            setup_limitations.append(
                "Search results were found, but no usable page content passed evidence validation."
            )
        self._emit(
            "Evidence processing completed",
            {
                "evidence_count": len(evidence),
                "evidence": [
                    {
                        "id": item.evidence_id,
                        "domain": item.root_domain,
                        "source_type": item.source_type,
                        "quality": item.source_quality,
                    }
                    for item in evidence
                ],
            },
        )
        source_credibility = assess_source_credibility(evidence)
        self._emit(
            "Source credibility scored",
            {
                "source_trust_score": source_credibility.source_trust_score,
                "website_risk_level": source_credibility.website_risk_level,
                "independent_source_count": source_credibility.independent_source_count,
                "risk_signals": source_credibility.risk_signals,
            },
        )

        verifier_outputs: list[VerifierOutput] = []
        verifier_limitations: list[str] = []
        expected_verifier_count = 0
        judge_output = None
        judge_limitations: list[str] = []
        if not evidence:
            setup_limitations.append(
                "Gonka verifier calls were skipped because there was no evidence to review."
            )
            self._emit(
                "Verifier steps skipped",
                {"reason": "No usable evidence was available."},
            )
        else:
            verifier_specs = [
                ("Verifier 1", "verifier_1", self.config.gonka_verify_model_1, "evidence_verifier.txt"),
                ("Verifier 2", "verifier_2", self.config.gonka_verify_model_2, "skeptical_reviewer.txt"),
            ]
            fallback_model = self.config.gonka_fallback_model
            primary_models = {
                self.config.gonka_verify_model_1,
                self.config.gonka_verify_model_2,
            }
            if fallback_model and fallback_model not in primary_models:
                verifier_specs.append(
                    ("Fallback verifier", "verifier_fallback", fallback_model, "evidence_verifier.txt")
                )
            expected_verifier_count = len(verifier_specs)
            failed_verifier_specs = []

            for label, _, model_id, _ in verifier_specs:
                self._emit(f"{label} started", {"model": model_id})

            with ThreadPoolExecutor(
                max_workers=len(verifier_specs),
                thread_name_prefix="verity-verifier",
            ) as executor:
                futures = [
                    executor.submit(
                        self._verify_with_model,
                        step_name=step_name,
                        model_id=model_id,
                        prompt_name=prompt_name,
                        claim=claim,
                        evidence=evidence,
                        source_credibility=source_credibility,
                    )
                    for _, step_name, model_id, prompt_name in verifier_specs
                ]
                verifier_results = [future.result() for future in futures]

            for spec, result in zip(verifier_specs, verifier_results):
                label, step_name, model_id, _ = spec
                output, output_traces, succeeded = result
                traces.extend(output_traces)
                self._emit(
                    f"{label} completed",
                    {
                        "model": model_id,
                        "verdict": output.verdict,
                        "support_score": output.support_score,
                        "confidence": output.confidence,
                        "supporting_evidence": output.supporting_evidence,
                        "contradicting_evidence": output.contradicting_evidence,
                        "included_in_consensus": succeeded,
                    },
                )
                if succeeded:
                    verifier_outputs.append(output)
                else:
                    failed_verifier_specs.append(spec)

            decisive_count = sum(output.verdict != "unverified" for output in verifier_outputs)
            if decisive_count < 2 and failed_verifier_specs:
                self._emit(
                    "Verifier quorum recovery started",
                    {
                        "decisive_outputs": decisive_count,
                        "retry_models": [spec[2] for spec in failed_verifier_specs],
                    },
                )
                with ThreadPoolExecutor(
                    max_workers=len(failed_verifier_specs),
                    thread_name_prefix="verity-recovery",
                ) as executor:
                    recovery_futures = [
                        executor.submit(
                            self._verify_with_model,
                            step_name=f"{step_name}_recovery",
                            model_id=model_id,
                            prompt_name=prompt_name,
                            claim=claim,
                            evidence=evidence,
                            source_credibility=source_credibility,
                        )
                        for _, step_name, model_id, prompt_name in failed_verifier_specs
                    ]
                    recovery_results = [future.result() for future in recovery_futures]

                recovered_models = []
                for spec, result in zip(failed_verifier_specs, recovery_results):
                    label, step_name, model_id, _ = spec
                    output, output_traces, succeeded = result
                    traces.extend(output_traces)
                    if succeeded:
                        verifier_outputs.append(output)
                        recovered_models.append(model_id)
                        verifier_limitations.append(
                            f"{label} failed initially but succeeded during quorum recovery."
                        )
                    else:
                        role = "fallback verifier" if step_name == "verifier_fallback" else label
                        verifier_limitations.append(
                            f"{role.capitalize()} failed twice and was excluded from consensus."
                        )
                self._emit(
                    "Verifier quorum recovery completed",
                    {
                        "recovered_models": recovered_models,
                        "decisive_outputs": sum(
                            output.verdict != "unverified" for output in verifier_outputs
                        ),
                    },
                )
            else:
                for label, step_name, _, _ in failed_verifier_specs:
                    role = "fallback verifier" if step_name == "verifier_fallback" else label
                    verifier_limitations.append(
                        f"{role.capitalize()} failed and was excluded from the consensus calculation."
                    )

        if self.config.decision_model and len(verifier_outputs) >= 2:
            self._emit(
                "Decision review started",
                {
                    "model": self.config.decision_model,
                    "verifier_count": len(verifier_outputs),
                },
            )
            judge_output, judge_traces, judge_limitations = self._review_final_decision(
                claim=claim,
                evidence=evidence,
                source_credibility=source_credibility,
                verifier_outputs=verifier_outputs,
            )
            traces.extend(judge_traces)
            self._emit(
                "Decision review completed",
                {
                    "model": self.config.decision_model,
                    "used_decision_review": judge_output is not None,
                    "verdict": judge_output.verdict if judge_output else None,
                    "support_score": judge_output.support_score if judge_output else None,
                    "confidence": judge_output.confidence if judge_output else None,
                    "limitations": judge_limitations,
                },
            )
        elif self.config.decision_model:
            judge_limitations.append(
                "Kimi decision review was skipped because fewer than two verifier outputs were available."
            )
            self._emit(
                "Decision review skipped",
                {
                    "model": self.config.decision_model,
                    "verifier_count": len(verifier_outputs),
                    "reason": "At least two verifier outputs are required.",
                },
            )
        elif len(verifier_outputs) == 2 and needs_judge(*verifier_outputs):
            verifier_1, verifier_2 = verifier_outputs
            self._emit(
                "Disagreement detected",
                {
                    "score_gap": abs(verifier_1.support_score - verifier_2.support_score),
                    "verdicts": [verifier_1.verdict, verifier_2.verdict],
                    "judge_model": self.config.judge_model,
                },
            )
            judge_output, judge_traces, judge_limitations = self._judge_disagreement(
                claim=claim,
                evidence=evidence,
                source_credibility=source_credibility,
                verifier_1=verifier_1,
                verifier_2=verifier_2,
            )
            traces.extend(judge_traces)
            self._emit(
                "Judge completed",
                {
                    "used_judge": judge_output is not None,
                    "verdict": judge_output.verdict if judge_output else None,
                    "support_score": judge_output.support_score if judge_output else None,
                    "confidence": judge_output.confidence if judge_output else None,
                    "limitations": judge_limitations,
                },
            )
        elif len(verifier_outputs) == 2:
            verifier_1, verifier_2 = verifier_outputs
            self._emit(
                "No judge needed",
                {
                    "score_gap": abs(verifier_1.support_score - verifier_2.support_score),
                    "verdicts": [verifier_1.verdict, verifier_2.verdict],
                },
            )

        self._emit("Consensus started", {"method": "confidence-weighted deterministic logic"})
        insufficient_evidence_explanation = None
        if not evidence and search_errors and not search_results:
            insufficient_evidence_explanation = (
                "Web search was unavailable, so no evidence-based truth verdict could be issued."
            )
        elif not evidence and search_results:
            insufficient_evidence_explanation = (
                "Search results were found, but no usable evidence could be retained for a truth verdict."
            )
        elif not evidence:
            insufficient_evidence_explanation = (
                "No web evidence was found, so the claim remains unverified."
            )
        consensus = build_consensus(
            verifier_outputs,
            evidence,
            judge_output=judge_output,
            source_credibility=source_credibility,
            insufficient_evidence_explanation=insufficient_evidence_explanation,
            expected_verifier_count=expected_verifier_count,
        )

        support_ids, contradict_ids = collect_evidence_ids(verifier_outputs, judge_output)
        supporting, contradicting = split_evidence_by_model_outputs(evidence, support_ids, contradict_ids)
        self._emit(
            "Consensus completed",
            {
                "final_verdict": consensus.final_verdict,
                "truth_score": consensus.truth_score,
                "confidence_score": consensus.confidence_score,
                "supporting_ids": sorted(support_ids),
                "contradicting_ids": sorted(contradict_ids),
            },
        )

        return FactCheckReport(
            extracted_claim=claim,
            deep_review=deep_review,
            extracted_claims=[claim],
            review_status="failed" if evidence and not verifier_outputs else "completed",
            final_verdict=consensus.final_verdict,
            truth_score=consensus.truth_score,
            confidence_score=consensus.confidence_score,
            concise_explanation=consensus.concise_explanation,
            supporting_evidence=supporting,
            contradicting_evidence=contradicting,
            all_evidence=evidence,
            source_credibility_assessment=source_credibility,
            verifier_outputs=verifier_outputs,
            judge_output=judge_output,
            gonka_trace=traces,
            limitations=(
                setup_limitations
                + query_limitations
                + verifier_limitations
                + judge_limitations
            ),
        )

    def _research_gaps(
        self, claim: str, evidence: list[EvidenceItem], initial_queries: list[str],
    ) -> tuple[list[EvidenceItem], DeepReview, list[GonkaTraceRecord]]:
        self._emit("Evidence gap review started", {"model": self.config.claim_model})
        try:
            plan, traces = self._call_json_validated(
                step_name="evidence_gap_review", model_id=self.config.claim_model,
                prompt_name="evidence_gap_review.txt",
                payload={"claim": claim, "evidence": [item.model_dump() for item in evidence],
                         "initial_queries": initial_queries},
                validator=validate_english_evidence_gap_plan,
            )
        except PipelineStepFailed as exc:
            note = "Professional evidence-gap analysis failed; the initial evidence was still reviewed."
            self._emit("Evidence gap review failed", {"reason": note})
            return evidence, DeepReview(status="failed", initial_source_count=len(evidence), limitations=[note]), exc.traces

        seen = {" ".join(query.split()).casefold() for query in initial_queries}
        follow_up = []
        for query in plan.follow_up_queries:
            query = " ".join(query.split())[:250]
            if query and query.casefold() not in seen:
                seen.add(query.casefold())
                follow_up.append(query)
        result = DeepReview(status="completed", summary=plan.summary, gaps=plan.gaps,
                            follow_up_queries=follow_up, initial_source_count=len(evidence))
        self._emit("Evidence gap review completed", {"summary": plan.summary, "gaps": plan.gaps, "queries": follow_up})
        if not follow_up:
            if plan.gaps:
                result.status = "partial"
                result.limitations.append("Evidence gaps were identified, but no new targeted queries were produced.")
            return evidence, result, traces

        self._emit("Follow-up research started", {"queries": follow_up, "query_count": len(follow_up)})
        try:
            self._preview_browser_searches(follow_up)
            results = self.search_provider.search_many(follow_up, max_results_per_query=self.max_results_per_query)
            errors = list(getattr(self.search_provider, "last_errors", []))
            fresh = self.evidence_processor.build_evidence(results)
            existing_urls = {item.url for item in evidence}
            fresh = [item for item in fresh if item.url not in existing_urls]
            # Reserve space for targeted findings before both verifiers read the final ledger.
            limit = getattr(self.evidence_processor, "max_evidence", 12)
            combined = remove_near_duplicates([*fresh[:min(4, limit)], *evidence])
            evidence = relabel_evidence(combined[:limit])
            result.additional_source_count = sum(item.url not in existing_urls for item in evidence)
            if errors or not result.additional_source_count:
                result.status = "partial"
                result.limitations.append("Follow-up research returned no additional usable sources." if not result.additional_source_count
                                          else "Some follow-up searches failed; available sources were retained.")
        except Exception:
            result.status = "partial"
            result.limitations.append("Follow-up research was unavailable; the initial evidence was still reviewed.")
        self._emit("Follow-up research completed", {"status": result.status, "additional_source_count": result.additional_source_count,
                                                   "evidence_count": len(evidence), "limitations": result.limitations})
        return evidence, result, traces

    def _emit(self, stage: str, details: dict[str, Any] | None = None) -> None:
        if self.progress_callback is None:
            return
        self.progress_callback(stage, {**self._claim_context, **(details or {})})

    def _preview_browser_searches(self, queries: list[str]) -> None:
        if self.browser_demo is None:
            return
        for index, query in enumerate(queries, start=1):
            try:
                self._emit(
                    "Visible browser search opened",
                    {"query_number": index, "query": query},
                )
                self.browser_demo.show_search(query)
            except Exception as exc:
                self._emit(
                    "Visible browser preview failed",
                    {"stage": "search", "safe_error_message": str(exc)[:300]},
                )
                return

    def _preview_browser_urls(self, urls: list[str]) -> None:
        if self.browser_demo is None:
            return
        for index, url in enumerate(urls, start=1):
            try:
                self._emit(
                    "Visible browser evidence opened",
                    {"url_number": index, "url": url},
                )
                self.browser_demo.show_url(url)
            except Exception as exc:
                self._emit(
                    "Visible browser preview failed",
                    {"stage": "evidence_url", "url": url, "safe_error_message": str(exc)[:300]},
                )

    def _ensure_ready(self) -> None:
        missing = self.config.missing_required_values()
        if missing:
            raise PipelineConfigError(f"Missing required configuration: {', '.join(missing)}")
        multi_model_issue = self.config.multi_model_issue()
        if multi_model_issue:
            raise PipelineConfigError(multi_model_issue)

    def _prepare_source_text(
        self,
        *,
        text: str,
        article_url: str,
    ) -> tuple[str, list[str], ArticleContent | None]:
        parts = []
        limitations: list[str] = []
        source_article: ArticleContent | None = None
        if text.strip():
            parts.append(text.strip())
        if article_url.strip():
            try:
                article = extract_article(article_url)
                source_article = article
                parts.append(f"Article title: {article.title}\nArticle text: {article.text}")
            except (ArticleFetchError, URLSafetyError) as exc:
                limitations.append(f"Article extraction failed: {exc}")
        if not parts:
            raise PipelineConfigError("Enter a text claim, an article URL, or both.")
        source_text = "\n\n".join(parts)
        if len(source_text) > 12000:
            limitations.append(
                "Claim extraction was limited to the first 12,000 characters; "
                "claims beyond that input window were not reviewed."
            )
        return source_text[:12000], limitations, source_article

    def _extract_claims(
        self,
        source_text: str,
        *,
        fallback_claim: str = "",
    ) -> tuple[ClaimExtraction, list[Any], list[str]]:
        payload = {"text": source_text}
        try:
            output, traces = self._call_json_validated(
                step_name="claim_extraction",
                model_id=self.config.claim_model,
                prompt_name="claim_extractor.txt",
                payload=payload,
                validator=validate_english_claim_extraction,
            )
            return output, traces, []
        except PipelineStepFailed as exc:
            candidate = deterministic_claim_fallback(fallback_claim, source_text)
            if not candidate:
                raise
            is_timeout = any(
                "timeout" in ((trace.error_type or "") + (trace.safe_error_message or "")).lower()
                for trace in exc.traces
            )
            reason = "Claim extraction timed out" if is_timeout else "Claim extraction failed"
            limitation = (
                f"{reason}; the submitted text or article title was used as a fallback claim. "
                "Additional claims could not be separated and were not independently reviewed."
            )
            self._emit(
                "Claim extraction fallback used",
                {"reason": reason, "fallback_claim": candidate},
            )
            return ClaimExtraction(claims=[candidate]), exc.traces, [limitation]

    def _plan_searches(self, claim: str) -> tuple[SearchQueries, list[Any], list[str]]:
        try:
            output, traces = self._call_json_validated(
                step_name="search_planning",
                model_id=self.config.claim_model,
                prompt_name="search_planner.txt",
                payload={"claim": claim},
                validator=SearchQueries.model_validate,
            )
            return output, traces, []
        except PipelineStepFailed as exc:
            return (
                deterministic_search_queries(claim),
                exc.traces,
                [f"Search planner failed; deterministic fallback queries were used: {exc}"],
            )

    def _verify_with_model(
        self,
        *,
        step_name: str,
        model_id: str,
        prompt_name: str,
        claim: str,
        evidence: list[EvidenceItem],
        source_credibility: SourceCredibilityAssessment,
    ) -> tuple[VerifierOutput, list[Any], bool]:
        allowed_ids = {item.evidence_id for item in evidence}
        payload = {
            "claim": claim,
            "evidence": [item.model_dump() for item in evidence],
            "source_credibility_assessment": source_credibility.model_dump(),
        }
        try:
            output, traces = self._call_json_validated(
                step_name=step_name,
                model_id=model_id,
                prompt_name=prompt_name,
                payload=payload,
                validator=lambda data: validate_verifier_output(data, allowed_ids),
            )
            return output.model_copy(update={"model_id": model_id}), traces, True
        except PipelineStepFailed as exc:
            fallback = VerifierOutput(
                model_id=model_id,
                verdict="unverified",
                support_score=50,
                confidence=10,
                supporting_evidence=[],
                contradicting_evidence=[],
                context_mismatch=False,
                reasoning_summary="Verifier failed validation or API call; claim remains unverified.",
                missing_information=[str(exc)],
            )
            return fallback, exc.traces, False

    def _judge_disagreement(
        self,
        *,
        claim: str,
        evidence: list[EvidenceItem],
        source_credibility: SourceCredibilityAssessment,
        verifier_1: VerifierOutput,
        verifier_2: VerifierOutput,
    ) -> tuple[VerifierOutput | None, list[Any], list[str]]:
        allowed_ids = {item.evidence_id for item in evidence}
        payload = {
            "claim": claim,
            "evidence": [item.model_dump() for item in evidence],
            "source_credibility_assessment": source_credibility.model_dump(),
            "verifier_1": verifier_1.model_dump(),
            "verifier_2": verifier_2.model_dump(),
        }
        try:
            output, traces = self._call_json_validated(
                step_name="disagreement_judge",
                model_id=self.config.judge_model,
                prompt_name="disagreement_judge.txt",
                payload=payload,
                validator=lambda data: validate_verifier_output(data, allowed_ids),
            )
            limitations = []
            if not self.config.gonka_judge_model:
                limitations.append(
                    "GONKA_JUDGE_MODEL was not configured; the first verifier model was used as judge."
                )
            return output, traces, limitations
        except PipelineStepFailed as exc:
            return None, exc.traces, [f"Judge step failed; deterministic consensus fallback was used: {exc}"]

    def _review_final_decision(
        self,
        *,
        claim: str,
        evidence: list[EvidenceItem],
        source_credibility: SourceCredibilityAssessment,
        verifier_outputs: list[VerifierOutput],
    ) -> tuple[VerifierOutput | None, list[Any], list[str]]:
        """Ask a dedicated Gonka model to audit the evidence and reviewer outputs."""
        allowed_ids = {item.evidence_id for item in evidence}
        payload = {
            "claim": claim,
            "evidence": [item.model_dump() for item in evidence],
            "source_credibility_assessment": source_credibility.model_dump(),
            "verifier_outputs": [item.model_dump() for item in verifier_outputs],
        }
        try:
            output, traces = self._call_json_validated(
                step_name="decision_review",
                model_id=self.config.decision_model,
                prompt_name="decision_reviewer.txt",
                payload=payload,
                validator=lambda data: validate_verifier_output(data, allowed_ids),
            )
            return output.model_copy(update={"model_id": self.config.decision_model}), traces, []
        except PipelineStepFailed as exc:
            return None, exc.traces, [
                "Kimi decision review failed; deterministic consensus from the independent "
                f"verifiers was used instead: {exc}"
            ]

    def _call_json_validated(
        self,
        *,
        step_name: str,
        model_id: str,
        prompt_name: str,
        payload: dict[str, Any],
        validator: Any,
    ) -> tuple[Any, list[Any]]:
        traces = []
        prompt = load_prompt(prompt_name)
        current_payload = payload
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                result = self.gonka_client.chat_json(
                    step_name=step_name if attempt == 0 else f"{step_name}_retry",
                    model_id=model_id,
                    prompt=prompt,
                    user_payload=current_payload,
                )
                trace = self._capture_trace(result.trace)
                traces.append(trace)
                self._emit(
                    "Gonka call completed",
                    {
                        "step_name": result.trace.step_name,
                        "requested_model_id": result.trace.requested_model_id,
                        "returned_model_id": result.trace.returned_model_id,
                        "response_body_id": result.trace.response_body_id,
                        "request_id": result.trace.request_id,
                        "trace_id": result.trace.trace_id,
                        "latency_ms": result.trace.latency_ms,
                    },
                )
                parsed = parse_json_object(result.text)
                return validator(parsed), traces
            except GonkaCallFailed as exc:
                traces.append(self._capture_trace(exc.trace))
                last_error = exc
                self._emit(
                    "Gonka call failed",
                    {
                        "step_name": exc.trace.step_name,
                        "requested_model_id": exc.trace.requested_model_id,
                        "request_id": exc.trace.request_id,
                        "trace_id": exc.trace.trace_id,
                        "latency_ms": exc.trace.latency_ms,
                        "error_type": exc.trace.error_type,
                        "safe_error_message": exc.trace.safe_error_message,
                    },
                )
                break
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = exc
                self._emit(
                    "Model output validation failed",
                    {
                        "step_name": step_name,
                        "attempt": attempt + 1,
                        "safe_error_message": str(exc)[:300],
                    },
                )
            current_payload = {
                **payload,
                "previous_output_error": str(last_error),
                "retry_instruction": "Return corrected strict JSON only.",
            }
        raise PipelineStepFailed(f"{step_name} failed: {last_error}", traces)

    def _capture_trace(self, trace: GonkaTraceRecord) -> GonkaTraceRecord:
        trace = trace.model_copy(
            update={key: value for key, value in self._claim_context.items() if key in {"claim_index", "claim"}}
        )
        if self._current_claim_traces is not None:
            self._current_claim_traces.append(trace)
        return trace


def unique_extracted_claims(claims: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for claim in claims:
        normalized = " ".join(claim.split())
        key = normalized.casefold().rstrip(".!?。！？")
        if key and key not in seen:
            seen.add(key)
            result.append(normalized)
    return result


def deterministic_claim_fallback(fallback_claim: str, source_text: str) -> str:
    candidate = " ".join(fallback_claim.split()).strip()
    if not candidate:
        for line in source_text.splitlines():
            if line.lower().startswith("article title:"):
                candidate = line.split(":", 1)[1].strip()
                break
    if not candidate:
        candidate = " ".join(source_text.split()).strip()
    return candidate[:500]


def validate_verifier_output(data: dict[str, Any], allowed_evidence_ids: set[str]) -> VerifierOutput:
    output = VerifierOutput.model_validate(data)
    require_english_generated_text(
        [output.reasoning_summary, *output.missing_information],
        "verifier explanation",
    )
    invalid_ids = output.referenced_evidence_ids() - allowed_evidence_ids
    if invalid_ids:
        raise ValueError(f"Model cited unknown Evidence IDs: {', '.join(sorted(invalid_ids))}")
    score_ranges = {
        "false": (0, 24),
        "mostly_false": (25, 44),
        "misleading": (45, 69),
        "mostly_true": (70, 84),
        "true": (85, 100),
    }
    if output.verdict == "unverified":
        if not 45 <= output.support_score <= 55:
            raise ValueError(
                "An unverified verdict must use a neutral support_score between 45 and 55."
            )
        return output
    if output.context_mismatch and output.verdict != "misleading":
        raise ValueError("context_mismatch=true requires a misleading verdict.")
    if output.verdict != "unverified":
        minimum, maximum = score_ranges[output.verdict]
        if not minimum <= output.support_score <= maximum:
            raise ValueError(
                f"support_score {output.support_score} is inconsistent with verdict "
                f"{output.verdict!r}; expected {minimum}-{maximum}."
            )
    return output


def validate_english_claim_extraction(data: dict[str, Any]) -> ClaimExtraction:
    output = ClaimExtraction.model_validate(data)
    require_english_generated_text(
        [*output.claims, output.not_verifiable_reason],
        "claim extraction",
    )
    return output


def validate_english_evidence_gap_plan(data: dict[str, Any]) -> EvidenceGapPlan:
    output = EvidenceGapPlan.model_validate(data)
    require_english_generated_text(
        [output.summary, *output.gaps, *output.follow_up_queries],
        "professional research explanation",
    )
    return output


def require_english_generated_text(values: list[str], field_name: str) -> None:
    if any(NON_ENGLISH_SCRIPT_PATTERN.search(value or "") for value in values):
        raise ValueError(f"{field_name} must be written in English; translate all non-English text")


def collect_evidence_ids(
    verifier_outputs: list[VerifierOutput],
    judge_output: VerifierOutput | None,
) -> tuple[set[str], set[str]]:
    support: set[str] = set()
    contradict: set[str] = set()
    outputs = [*verifier_outputs]
    if judge_output is not None:
        outputs.append(judge_output)
    for output in outputs:
        support.update(output.supporting_evidence)
        contradict.update(output.contradicting_evidence)
    return support, contradict


def article_to_evidence(article: ArticleContent) -> EvidenceItem:
    source_type, source_quality = classify_source(article.url)
    return EvidenceItem(
        evidence_id="E0",
        title=article.title or article.url,
        url=article.url,
        root_domain=root_domain(article.url),
        publisher=article.publisher or publisher_from_url(article.url),
        published_date=article.published_date,
        retrieved_at="source_article",
        excerpt=normalize_excerpt(article.text),
        source_type=source_type,
        source_quality=source_quality,
    )
