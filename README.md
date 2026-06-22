# TTB Label Verification App

Proof-of-concept web app for checking alcohol/tobacco label photos against expected TTB application data. It supports one-label checks and batch checks, returns a plain-language pass/review result, and keeps the government warning comparison exact and case-sensitive.

## Live Demo

- App: `DEPLOYED_URL_PENDING`
- Health check: `DEPLOYED_URL_PENDING/health`

The submission deployment is intended to run with `USE_MOCK_VISION=true` so reviewers can test the full UI/API flow without external API quota or billing dependencies.

## What It Does

- Upload one label photo or a batch of up to 5 label photos.
- Enter expected application values for brand, class/type, producer, country, ABV, bottle size, and government warning.
- Extract label values through a mockable vision service.
- Compare normal fields with fuzzy/normalized matching.
- Compare the government warning with an exact, case-sensitive match.
- Return `APPROVED` when all fields pass, or `NEEDS REVIEW` when any field fails or cannot be read.

## Approach

The backend is a stateless FastAPI app. Uploaded images are validated, resized, JPEG-encoded, and passed to a `VisionService`. The default submission deployment uses `MockVisionService`; the OpenAI-backed service is available when `USE_MOCK_VISION=false` and `OPENAI_API_KEY` is configured.

The comparison layer is intentionally stricter for the government warning than for other fields:

- Brand, class/type, and producer use fuzzy matching.
- Country uses simple synonym normalization.
- ABV and net contents use numeric/unit normalization.
- Government warning must match exactly, including capitalization, punctuation, spacing, and line breaks.

The frontend is plain HTML/CSS/JavaScript served by FastAPI. It is designed for non-technical users with large text, clear buttons, visible focus styles, simple error messages, and accessible labels/live regions.

## Tools And Stack

- Python + FastAPI
- Pydantic
- Pillow for image preprocessing
- RapidFuzz for fuzzy field comparison
- OpenAI Python SDK support for real vision extraction
- Plain HTML/CSS/JavaScript frontend
- Railway deployment

## Local Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create local environment settings:

```bash
cp ../.env.example ../.env
```

For local/demo testing, keep:

```bash
USE_MOCK_VISION=true
```

Run the app:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/health`

## Environment Variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `USE_MOCK_VISION` | Use deterministic mock extraction instead of OpenAI. | `false` unless set |
| `OPENAI_API_KEY` | Required only for real OpenAI vision mode. | unset |
| `OPENAI_VISION_MODEL` | Vision model for real OpenAI mode. | `gpt-4o-mini` |
| `OPENAI_TIMEOUT_SECONDS` | OpenAI request timeout. | `3.8` |
| `OPENAI_IMAGE_DETAIL` | OpenAI image detail setting. | `low` |
| `PREPROCESS_MAX_DIMENSION` | Long-edge image resize target. | `768` |
| `PREPROCESS_JPEG_QUALITY` | JPEG quality after preprocessing. | `75` |
| `BATCH_CONCURRENCY` | Max concurrent batch item checks. | `3` |

Secrets must stay in local `.env` files or deployment environment variables. Do not commit real API keys.

## Testing

Run the backend tests:

```bash
cd backend
python -m pytest tests -q
```

Run the live submission checklist after deployment:

```bash
python scripts/phase6_live_checklist.py --mock-vision DEPLOYED_URL
```

The live checklist covers valid label, mismatches, case-only normalization, ABV/unit normalization, correct/wrong/missing warning, imperfect image, wrong file type, empty submit, batch summary, and single-label latency.

## Deployment

Railway is configured by `railway.toml` with `backend` as the root directory and this start command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Set Railway environment variables:

```bash
USE_MOCK_VISION=true
PREPROCESS_MAX_DIMENSION=768
PREPROCESS_JPEG_QUALITY=75
BATCH_CONCURRENCY=3
```

For real OpenAI extraction, additionally set `OPENAI_API_KEY` and set `USE_MOCK_VISION=false`.

## Assumptions

- The app is stateless and uses no database.
- Batch uploads are limited to 5 labels.
- Final submission/demo mode uses MockVision for repeatable testing.
- Real vision extraction depends on configured OpenAI credentials and quota.

## Limitations

- This is a proof of concept, not a production compliance system.
- MockVision validates the application flow but does not perform real OCR/vision.
- Real extraction quality depends on image clarity, lighting, orientation, and model availability.
- Uploaded images are processed in memory and are not stored.
