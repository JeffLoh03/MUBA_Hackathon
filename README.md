# Gonka AI Fact Checker / Verity Desk

Hackathon MVP for evidence-led news verification through Gonka Router.

The primary interface is the React + Vite frontend from the team repository, connected to the Python verification pipeline in this project. `app.py` remains only as a legacy Streamlit fallback.

## What Makes It Different

The app does not ask one model to guess whether an article is true. It separates verification into:

- Claim truth: extract a checkable claim and find supporting and contradicting evidence.
- Source trust: score independence, quality, official-source coverage, missing dates and syndication risk.
- Model review: DeepSeek, Kimi and MiniMax independently assess the evidence through Gonka Router.
- Deterministic consensus: fixed rules combine model output with source credibility and can force a weak case back to `Unverified`.

During a review, the React page receives real progress events from Python. It shows search activity, evidence counts, source scoring and model stages without exposing private chain-of-thought. The final audit trail keeps Gonka response IDs separate from request and trace headers.

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
choco install tesseract
```

## Gonka Configuration

Create the local environment file if it does not exist:

```powershell
Copy-Item .env.example .env
notepad .env
```

The current three-verifier setup is:

```env
GONKA_BASE_URL=https://api.gonkarouter.io/v1
GONKA_API_KEY=your-real-gonka-key
GONKA_TIMEOUT_SECONDS=60

GONKA_CLAIM_MODEL=moonshotai/Kimi-K2.6
GONKA_VERIFY_MODEL_1=deepseek-ai/DeepSeek-V4-Flash-0731
GONKA_VERIFY_MODEL_2=moonshotai/Kimi-K2.6
GONKA_JUDGE_MODEL=moonshotai/Kimi-K2.6
GONKA_FALLBACK_MODEL=MiniMaxAI/MiniMax-M2.7
GONKA_VISION_MODEL=

SEARCH_PROVIDER=duckduckgo
TAVILY_API_KEY=
```

Never send `.env` to anyone and never commit it to GitHub. It is already ignored by Git.

## Run The React App

The React build is served by the Python API, so one command is enough after `npm run build`:

```powershell
python -m uvicorn api:app --reload --port 8000
```

Open `http://127.0.0.1:8000`.

The main composer accepts three input types:

- Paste a complete article URL.
- Type or paste a factual claim directly.
- Click `+` or drag in a JPG, PNG, or WEBP image up to 10 MB. Text in the composer becomes the image caption or contextual claim.

Press Enter to verify or Shift+Enter for a new line. Uploaded images are processed in memory and are not saved permanently.

Review modes:

- `Quick review` accepts a short direct-text claim without an extra extraction call, uses compact deterministic search queries with common organization aliases, and retains up to 5 evidence sources.
- `Professional review` asks the configured Gonka planning model for deeper queries and retains up to 12 evidence sources.

Both modes run DeepSeek, Kimi and the configured fallback model concurrently. A failed model call is recorded in the audit trail and excluded from consensus rather than counted as an `Unverified` vote. If fewer than two decisive outputs return, failed models receive one parallel quorum-recovery attempt. At least two decisive model outputs are still required for a firm verdict. With three valid outputs, the deterministic consensus uses the median support score so one outlier cannot overturn two agreeing models. MiniMax improves availability, but its output alone is displayed as `Unverified` rather than treated as consensus.

For frontend development with instant React refresh, use two PowerShell windows.

Backend:

```powershell
cd MUBA_Hackathon
.\.venv\Scripts\Activate.ps1
python -m uvicorn api:app --reload --port 8000
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
python scripts\gonka_smoke_test.py --model "moonshotai/Kimi-K2.6"
```

The smoke test writes a secret-safe `test_results.json`.

## Offline Validation

```powershell
python -m pytest
cd frontend
npm run lint
npm run build
```

Python tests use mocks and do not call Gonka or search providers.

GitHub Actions runs the same Python tests, frontend lint and frontend build for every push and pull request. The workflow does not receive a Gonka API key and cannot make paid live requests.

## Live Evaluation

With the local API running and your Gonka key configured, run the reusable real-world evaluation set:

```powershell
python scripts\live_evaluation.py
```

It checks Chinese and English claims, true and false cases, multiple source domains, and an invented claim that must remain `Unverified`. The secret-safe summary is written to `live_evaluation_results.json`.

## Architecture

- `frontend/`: teammate React, TypeScript and Vite interface, now connected to real data
- `api.py`: FastAPI health check and NDJSON progress stream
- `pipeline/text_pipeline.py`: text/URL-to-report verification workflow with timeout fallback
- `pipeline/image_pipeline.py`: image OCR, metadata and context-to-text workflow
- `services/gonka_client.py`: Gonka calls, safe errors and request/trace capture
- `services/search_provider.py`: DuckDuckGo or optional raw Tavily search
- `services/evidence_processor.py`: page extraction, ranking and deduplication
- `services/source_credibility.py`: website and source-risk assessment
- `pipeline/consensus.py`: deterministic final verdict rules
- `app.py`: legacy Streamlit fallback

## Boundaries

- Weak evidence produces `Unverified`; missing citations and request IDs are never invented.
- The app evaluates article claims and source context, not pixel-level deepfake authenticity.
- Tavily is optional and is used only for raw search, never for LLM reasoning.
