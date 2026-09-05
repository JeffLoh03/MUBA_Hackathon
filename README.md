# Gonka AI Fact Checker / Verity Desk

Hackathon MVP for evidence-led news verification through Gonka Router.

The primary interface is the React + Vite frontend, connected to the packaged Python backend. `backend/streamlit_app.py` remains only as a legacy Streamlit fallback.

## Video Demonstration

[Watch the video demonstration on YouTube](https://youtu.be/y6r1veRv1LQ).

## What Makes It Different

The app does not ask one model to guess whether an article is true. It separates verification into:

- Claim truth: extract a checkable claim and find supporting and contradicting evidence.
- Source trust: score independence, quality, official-source coverage, missing dates and syndication risk.
- Model review: DeepSeek and MiniMax independently assess the evidence through Gonka Router.
- Final decision review: Kimi K2.6 audits the evidence, source-risk assessment, and both verifier outputs through Gonka before consensus rules are applied.
- Deterministic consensus: fixed rules combine model output with source credibility and can force a weak case back to `Unverified`.

Generated report content is always requested in English. Non-English model explanations, extracted claims, and Professional research summaries are rejected and retried before they can be saved. Original submitted text, OCR text, source quotations, names, and URLs remain in their original form so the audit record is faithful to the evidence.

During a review, the React page receives real progress events from Python. It shows search activity, evidence counts, source scoring and model stages without exposing private chain-of-thought. The final audit trail keeps Gonka response IDs separate from request and trace headers.

## Current Features

- Text, article URL, and image-caption investigations.
- Quick and Professional review modes with clearly different research depth.
- Up to three claims extracted and verified independently from one submission.
- Side-by-side claim comparison with separate verdicts, truth scores, confidence, evidence, and model agreement.
- Expandable evidence quotations and links to every retained source.
- A Gonka compliance summary showing the verifier models, agreement, successful and failed calls, and identifier coverage.
- SQLite transparency history with readable investigation titles, live progress recovery, complete reports, events, and Gonka request IDs.
- First-owner account setup, password login, private account-scoped history, session expiry, and rate limits.
- English generated reports. Original input, OCR text, URLs, and source quotations remain unchanged for audit accuracy.
- Tesseract OCR, image metadata inspection, and caption-based evidence research.

## Hackathon Requirement Coverage

| Requirement | Implementation |
| --- | --- |
| Gonka Router integration | Claim extraction, Professional search planning, evidence-gap review, two independent verifiers, and disagreement judging run through the configured Gonka gateway. |
| Multi-model consensus | Two distinct verifier models review the same evidence independently. A firm verdict requires at least two decisive outputs. |
| Claim or article input | The React interface accepts direct text, article URLs, and captioned images. |
| Decentralized verification | Open-web evidence is retrieved and supplied to Gonka-hosted models; failures stay visible and are excluded from consensus. |
| Truth score and reasoning | Every verified claim has its own verdict, truth score, confidence, and concise public reasoning. `Unverified` claims show an undetermined truth score. |
| Transparency UI | SQLite stores the report, public progress events, model IDs, latency, token usage, response IDs, request IDs, and trace IDs. |

`Unverified` means the available evidence or successful model responses were insufficient for a firm conclusion. It does not mean the claim is false.

## Requirements

- Python 3.11+
- Node.js 20.19+
- A Gonka Router API key
- Chrome or Playwright Chromium for the optional visible-browser demonstration
- Tesseract OCR for reading text from screenshots and images

You do not need an OpenAI API key. The official OpenAI Python package is used only as an OpenAI-compatible client for Gonka Router.

## Windows PowerShell Setup

Clone the team repository, then enter the project folder:

```powershell
git clone https://github.com/JeffLoh03/MUBA_Hackathon.git
cd MUBA_Hackathon
```

If the repository is already on your computer, run `git pull` in that folder instead.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Install the React dependencies and create a production build:

```powershell
cd frontend
npm install
npm run build
cd ..
```

For the optional popup browser:

```powershell
python -m playwright install chromium
```

Install Tesseract OCR if `tesseract --version` is not available:

```powershell
winget install --id UB-Mannheim.TesseractOCR --exact --source winget
```

## Gonka Configuration

Create the local environment file if it does not exist:

```powershell
# for mac user use 'cp' instead of 'Copy-Item'
Copy-Item .env.example .env 
notepad .env
```

The two-verifier configuration tested successfully in this workspace is:

```env
GONKA_BASE_URL=https://api.gonkarouter.io/v1
GONKA_API_KEY=your-real-gonka-key
GONKA_TIMEOUT_SECONDS=60

GONKA_CLAIM_MODEL=MiniMaxAI/MiniMax-M2.7
GONKA_VERIFY_MODEL_1=deepseek-ai/DeepSeek-V4-Flash-0731
GONKA_VERIFY_MODEL_2=MiniMaxAI/MiniMax-M2.7
GONKA_JUDGE_MODEL=MiniMaxAI/MiniMax-M2.7
GONKA_DECISION_MODEL=moonshotai/Kimi-K2.6
GONKA_FALLBACK_MODEL=
GONKA_VISION_MODEL=

SEARCH_PROVIDER=duckduckgo
TAVILY_API_KEY=
```

Never send `.env` to anyone and never commit it to GitHub. It is already ignored by Git.

## Run The React App

The React build is served by the Python API, so one command is enough after `npm run build`:

```powershell
python -m uvicorn backend.api:app --reload --host 127.0.0.1 --port 8000 --no-proxy-headers
```

Open `http://127.0.0.1:8000`.

The main composer accepts three input types:

- Paste a complete article URL.
- Type or paste a factual claim directly.
- Click `+` or drag in a JPG, PNG, or WEBP image up to 10 MB. Text in the composer becomes the image caption or contextual claim.

Press Enter to verify or Shift+Enter for a new line. Uploaded images are processed in memory and are not saved permanently.

Review modes:

| Capability | Quick review | Professional review |
| --- | --- | --- |
| Intended use | Fast check of a focused claim | Articles, multiple claims, statistics, or disputed context |
| Claim extraction | Direct handling for a short single claim; AI extraction when needed | AI claim extraction on every submission |
| Search planning | Deterministic query plan | Gonka-generated research plan |
| Research passes | One | Initial research plus evidence-gap analysis and up to three targeted follow-up searches |
| Evidence retained | Up to 5 sources per claim | Up to 12 sources per claim |
| Independent verification | Two Gonka models | Two Gonka models using the expanded evidence |
| Typical speed and usage | Faster and fewer model calls | Slower and more model calls |

Both modes run the two distinct configured verifier models concurrently. A failed call is recorded and excluded from consensus. If fewer than two decisive outputs return, failed models receive one quorum-recovery attempt. A firm verdict requires at least two decisive outputs. An optional third model can be set with `GONKA_FALLBACK_MODEL`; with three valid outputs, consensus uses the median support score. When two verifier outputs are available, `moonshotai/Kimi-K2.6` performs a separate final decision review through Gonka. If that call fails, its failed request remains visible in the audit ledger and the deterministic verifier consensus is used safely instead.

Both modes can independently verify up to three extracted claims. Select a claim in the final report to see its own evidence, verdict and scores. Extra extracted claims are listed as unreviewed. There is no aggregate article truth score. Quick review skips extraction for a short, single-statement input and performs one research pass. Professional always uses AI extraction and planning and adds the evidence-gap assessment. Older saved reports remain unchanged and show no additional research assessment.

Tesseract is discovered automatically on PATH or in common Windows installation locations. Set `TESSERACT_CMD` to its executable if installed elsewhere. The current Windows install includes English OCR. Other languages need additional Tesseract language data and language configuration. The live Gonka image probe rejected `image_url`, so `GONKA_VISION_MODEL` remains empty: image checks use OCR, captions and metadata, not visual authenticity detection.

## Account Setup and Deployment

Start the backend bound to localhost with `--no-proxy-headers`, then open `/login` and create your owner account using a unique 12–128 character password. First-account setup is local-only and closes atomically after creation. Existing unowned audit records are assigned to this first account. There is no public signup or password-reset flow.

Verification and audit APIs require login. Passwords are PBKDF2-hashed; revocable server-side sessions expire after 12 hours. Cookies are HttpOnly and SameSite Strict. Audit queries are account-scoped; login and verification requests are rate-limited. Never commit the SQLite database, which contains investigation history and password hashes.

Before public deployment, finish owner setup locally, use HTTPS, and set `VERITY_COOKIE_SECURE=true`. Preserve the original Host header at your reverse proxy and keep `--no-proxy-headers`; first-account setup must not be exposed through a proxy. Do not expose the unauthenticated legacy Streamlit interface publicly. Authentication is an MVP control, not a full production security audit.

## Transparency Database

Every verification is recorded in a local SQLite database at `data/verity_desk.db` by default. The database stores:

- the submitted text, URL, or image filename (never the uploaded image bytes)
- the selected review mode, status, timestamps, verdict, truth score, and confidence
- the public progress-event timeline
- the final evidence report
- each Gonka step's model ID, response ID, request ID, trace ID, latency, token usage, and safe failure state

Open `http://127.0.0.1:8000/transparency` or select **Transparency** in the header to inspect the ledger. The JSON endpoints are `GET /api/audits` and `GET /api/audits/{run_id}`.

Select an investigation card to reopen its live progress page or completed report. Use **View audit details** for the stored operational record. Multi-claim history cards use the submitted text or a readable article title instead of the generic `Multiple claims reviewed` label.

The report UI separates three ideas:

- `Truth score` measures how strongly the retained evidence supports the complete claim.
- `Confidence` measures certainty in that assessment after evidence quality, model availability, and source risk are considered.
- `Model agreement` reports whether the independent verifiers reached compatible conclusions. One successful model is insufficient for a firm verdict.

Override the database location when needed:

```env
VERITY_DB_PATH=data/verity_desk.db
```

For Docker, mount persistent storage at `/app/data`. Private chain-of-thought and API credentials are never stored in the audit database.

For frontend development with instant React refresh, use two PowerShell windows.

Backend:

```powershell
cd MUBA_Hackathon
.\.venv\Scripts\Activate.ps1
python -m uvicorn backend.api:app --reload --host 127.0.0.1 --port 8000 --no-proxy-headers
```

Frontend:

```powershell
cd MUBA_Hackathon\frontend
npm run dev
```

Then open `http://127.0.0.1:5173`. Vite proxies `/api` calls to the Python backend.

## Watch Chrome Search

Turn on `Show live browser window` before starting verification. The local Python process opens Chrome, shows each DuckDuckGo query, and opens candidate evidence pages. The browser closes after the report finishes.

This only works when the API runs on your own computer. A hosted server cannot pop open Chrome on a judge's laptop.

## Gonka Smoke Test

```powershell
python scripts\gonka_smoke_test.py --list
python scripts\gonka_smoke_test.py --test-first 3
python scripts\gonka_smoke_test.py --configured
```

The smoke test writes a secret-safe `test_results.json`.

## Offline Validation

```powershell
python -m pytest
cd frontend
npm run lint
npm run build
```

Python tests use mocks and do not call Gonka or search providers. These checks run locally and do not require GitHub Actions.

## Live Evaluation

With the local API running and your Gonka key configured, run the reusable real-world evaluation set:

```powershell
python scripts\live_evaluation.py --email your-account@example.com
```

Enter your desk password at the hidden prompt. It checks Chinese and English claims, true and false cases, multiple source domains, and an invented claim that must remain `Unverified`. The secret-safe summary is written to `live_evaluation_results.json`.

## Project Structure

```text
MUBA_Hackathon/
├── backend/
│   ├── api.py                 # FastAPI and NDJSON streaming endpoints
│   ├── config.py              # Environment configuration
│   ├── database.py            # SQLite audit storage and query layer
│   ├── streamlit_app.py       # Legacy local interface
│   ├── pipeline/              # Verification and consensus workflows
│   ├── services/              # Gonka, search, extraction, OCR, and ranking
│   ├── schemas/               # Shared Pydantic data models
│   └── prompts/               # Version-controlled model instructions
├── frontend/                  # React, TypeScript, and Vite interface
├── scripts/                   # Live smoke and evaluation commands
├── tests/                     # Backend and API regression tests
├── .github/workflows/         # Automated validation
├── Dockerfile
└── requirements.txt
```

The main production entry point is `backend.api:app`. Run the legacy interface with
`python -m streamlit run backend/streamlit_app.py` when needed.

## Boundaries

- Weak evidence produces `Unverified`; missing citations and request IDs are never invented.
- The app evaluates article claims and source context, not pixel-level deepfake authenticity.
- Tavily is optional and is used only for raw search, never for LLM reasoning.
