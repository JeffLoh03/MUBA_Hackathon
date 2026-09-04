# Verity Desk Architecture

## Request Flow

1. `frontend/src/components/VerificationForm.tsx` accepts a claim, article URL, or image and selects quick or professional review.
2. `frontend/src/hooks/useVerification.ts` sends text/URL input to `/api/verify/stream` or image input to `/api/verify/image/stream`.
3. `api.py` creates the pipelines in a worker thread and streams newline-delimited JSON progress, report, or safe error events back to React.
4. `pipeline/text_pipeline.py` prepares the input, identifies the claim, plans searches, gathers evidence, runs independent verifiers, and builds the report.
5. `services/search_provider.py` runs DDGS metasearch with DuckDuckGo/Bing fallbacks, or optional Tavily, concurrently. Quick review uses three deterministic queries; professional review lets Gonka plan six deeper queries.
6. `services/evidence_processor.py` fetches candidate pages concurrently, rejects irrelevant claim-entity matches, removes duplicate coverage, and retains the best sources.
7. `services/source_credibility.py` scores source quality, domain independence, official coverage, dates, and syndication risk separately from claim truth.
8. `services/gonka_client.py` calls Gonka through the official OpenAI SDK, enforces a hard request deadline, redacts secrets, and records body, request, and trace IDs separately.
9. `pipeline/consensus.py` applies deterministic quorum and score rules. Weak evidence or fewer than two decisive model results becomes `Unverified` even when one model sounds confident.
10. `frontend/src/components/ReportView.tsx` renders the verdict, evidence, source-risk assessment, model opinions, limitations, and audit IDs.

## Text And URL Modes

Quick review skips two optional planning calls for short direct claims, uses deterministic multilingual search variants, and keeps up to five evidence items. Professional review uses Gonka claim extraction and search planning and keeps up to twelve evidence items. Article URLs are downloaded by `services/article_extractor.py`; the title is available as a deterministic claim fallback when model extraction fails.

## Image Mode

`pipeline/image_pipeline.py` validates the upload, runs OCR and reads safe metadata through `services/image_processor.py`. A configured vision model can describe claim context; otherwise OCR and the user's caption become a text claim. The downstream evidence and consensus path is the same as text mode. This checks the claim around an image, not pixel-level deepfake authenticity.

## Reliability Rules

- DeepSeek and Kimi are the two primary reviewers; MiniMax is an optional parallel standby.
- Verifier and judge calls allow up to 4096 output tokens because reasoning tokens share the same budget.
- The SDK does not retry invisibly. The pipeline records failures and controls recovery itself.
- Timeouts are not retried immediately. Exhausted JSON-format retries do not enter a second recovery layer.
- A model that failed its first verifier call is not immediately called again as judge.
- A firm verdict requires at least two decisive model outputs and usable web evidence.
- The deterministic median protects a three-model result from one extreme outlier.

## Test Layers

- `tests/test_mvp_core.py`: offline pipeline, consensus, search, evidence, timeout, redaction, and retry tests.
- `tests/test_api.py`: offline API validation and stream tests.
- `tests/test_smoke_cli.py`: offline smoke-test CLI behavior.
- `tests/test_live_evaluation.py`: offline evaluation-quality gate tests.
- `scripts/gonka_smoke_test.py`: real account/model connectivity test.
- `scripts/live_evaluation.py`: real bilingual end-to-end benchmark with evidence and quorum gates.
