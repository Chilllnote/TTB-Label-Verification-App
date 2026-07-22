# TTB Label Verification App

Proof-of-concept web app for checking alcohol/tobacco label photos against expected TTB application data. It supports one-label checks and batch checks, returns a plain-language pass/review result, and keeps the government warning comparison exact and case-sensitive.

## Live Demo

- App: `https://ttb-label-verification-app-v2-production.up.railway.app`
- Health check: `https://ttb-label-verification-app-v2-production.up.railway.app/health`

The current deployment is the fresh Railway `TTB-Label-Verification-App-v2` service, configured to run the real OpenAI vision service with `USE_MOCK_VISION=false`. A valid `OPENAI_API_KEY` must be present in Railway environment variables for `/verify` to return successful real-provider results.

## What It Does

- Upload one label photo or a batch of up to 5 label photos.
- Enter expected application values for brand, class/type, producer, country, ABV, bottle size, and government warning.
- Extract label values through a mockable vision service.
- Compare normal fields with fuzzy/normalized matching.
- Compare the government warning with an exact, case-sensitive match.
- Return `APPROVED` when all fields pass, or `NEEDS REVIEW` when any field fails or cannot be read.

## Approach

The backend is a stateless FastAPI app. Uploaded images are validated, resized, JPEG-encoded, and passed to a `VisionService`. The submission deployment uses `OpenAIVisionService`; `MockVisionService` is available only as a local development convenience when `USE_MOCK_VISION=true`.

The comparison layer is intentionally stricter for the government warning than for other fields:

- Brand, class/type, and producer use fuzzy matching with a 90% threshold.
- Country uses simple synonym normalization.
- If the vision model leaves country or producer blank, the backend conservatively fills them from clear raw label text such as `PRODUCED IN CANADA` or `Produced and Bottled By Lighthouse Vintners Kingston, NY`.
- Real vision requests include the uploaded view plus an upside-down view in the same model call, so labels whose pixels are actually flipped have a better chance of being read without a second provider request.
- The real vision response allows enough completion tokens for structured JSON with warning text and concise raw text, avoiding truncated JSON responses.
- ABV uses numeric/proof normalization with ±0.1% tolerance; net contents uses unit normalization.
- Government warning must match exactly, including capitalization, punctuation, spacing, and line breaks.
- Vision provider failures return a distinct unreadable-photo error instead of field mismatches.

The frontend is plain HTML/CSS/JavaScript served by FastAPI. It is designed for non-technical users with large text, clear buttons, visible focus styles, simple error messages, and accessible labels/live regions.

## Tools And Stack

- Python + FastAPI
- Pydantic
- Pillow for image preprocessing
- RapidFuzz for fuzzy field comparison
- OpenAI Python SDK support for real vision extraction
- Plain HTML/CSS/JavaScript frontend
- Railway deployment

Exact vision model: `gpt-4o-mini`, configured by `OPENAI_VISION_MODEL` and used by `OpenAIVisionService`. On July 12, 2026, this model was checked against OpenAI's current model documentation; the GPT-4o mini page lists text and image input, text output, Structured Outputs support, and the `gpt-4o-mini` alias/snapshot family.

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

For local mock testing only, set:

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
| `OPENAI_TIMEOUT_SECONDS` | OpenAI request timeout. | `20` |
| `OPENAI_IMAGE_DETAIL` | OpenAI image detail setting. | `high` |
| `PREPROCESS_MAX_DIMENSION` | Long-edge image resize target. Images are only downscaled, never upscaled. | `1024` |
| `PREPROCESS_JPEG_QUALITY` | JPEG quality after preprocessing. | `70` |
| `PREPROCESS_GRAYSCALE` | Convert image to grayscale before sending to vision. | `true` |
| `PREPROCESS_THRESHOLD` | Optional black/white threshold mode: `off`, `binary`, or `adaptive`. | `off` |
| `PREPROCESS_CONTRAST` | Apply light contrast/sharpening before threshold-capable preprocessing. | `true` |
| `BATCH_CONCURRENCY` | Max concurrent batch item checks. | `3` |

Secrets must stay in local `.env` files or deployment environment variables. Do not commit real API keys.

For local runs, `OPENAI_VISION_MODEL`, `OPENAI_TIMEOUT_SECONDS`, `OPENAI_IMAGE_DETAIL`, `PREPROCESS_MAX_DIMENSION`, `PREPROCESS_JPEG_QUALITY`, `PREPROCESS_GRAYSCALE`, `PREPROCESS_THRESHOLD`, `PREPROCESS_CONTRAST`, and `BATCH_CONCURRENCY` are read from the project `.env` file when that file exists. Railway does not receive `.env`, so the deployed container reads those same keys from Railway environment variables.

## Testing

Run the backend tests:

```bash
cd backend
python -m pytest tests -q
```

Run the real-provider live smoke check after deployment:

```bash
python scripts/live_check.py DEPLOYED_URL
```

The live smoke check uploads a generated JPEG label with non-mock values and fails if the deployment returns the mock service's fixed defaults. The broader live checklist in `scripts/phase6_live_checklist.py` can still be run for endpoint behavior after the real-provider smoke check passes.

Run the live latency benchmark:

```bash
python scripts/benchmark_live.py DEPLOYED_URL 10 30
```

The benchmark uses the same generated high-contrast JPEG synthetic label as `scripts/live_check.py` and reports p50/p95 latency for successful `/verify` responses.

## Performance

Target: one-label verification should complete in under 5 seconds.

Latest deployed measurement, run July 12, 2026:

```bash
cd backend
.venv/bin/python scripts/benchmark_live.py https://ttb-label-verification-app-v2-production.up.railway.app 5 30
```

Result:

| Metric | Value |
| --- | --- |
| Successful `/verify` attempts | `0 / 5` |
| Successful p50 latency | Not available |
| Successful p95 latency | Not available |
| All-attempt p50 latency | `882.7 ms` |
| All-attempt p95 latency | `1057.8 ms` |
| HTTP statuses | `[503, 503, 503, 503, 503]` |

The current Railway app is reachable (`/health` returns `{"status":"ok"}` on July 21, 2026). The successful real-provider p50/p95 required for final review still needs to be rerun after any deployment/configuration changes.

Railway cold-start note: the first request after inactivity may include container startup time. Treat the first benchmark sample separately when collecting final p50/p95 numbers for the 5-second target.

## API Examples

Set the deployed base URL:

```bash
BASE_URL="https://ttb-label-verification-app-v2-production.up.railway.app"
```

Single-label `/verify` request:

```bash
APP_JSON='{
  "brand": "Cedar Ridge Smoke Test",
  "class": "Red Wine",
  "producer": "Northstar Test Winery",
  "country": "United States of America",
  "abv": "13.5%",
  "net_contents": "750 ml",
  "government_warning": "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink alcoholic beverages during pregnancy because of the risk of birth defects. (2) Consumption of alcoholic beverages impairs your ability to drive a car or operate machinery, and may cause health problems."
}'

curl -sS -X POST "$BASE_URL/verify" \
  -F "image=@sample-label.jpg;type=image/jpeg" \
  -F "application_data=$APP_JSON"
```

Batch `/verify/batch` request:

```bash
BATCH_JSON='[
  {
    "brand": "Cedar Ridge Smoke Test",
    "class": "Red Wine",
    "producer": "Northstar Test Winery",
    "country": "United States of America",
    "abv": "13.5%",
    "net_contents": "750 ml",
    "government_warning": "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink alcoholic beverages during pregnancy because of the risk of birth defects. (2) Consumption of alcoholic beverages impairs your ability to drive a car or operate machinery, and may cause health problems."
  },
  {
    "brand": "Cedar Ridge Smoke Test",
    "class": "Red Wine",
    "producer": "Northstar Test Winery",
    "country": "United States of America",
    "abv": "13.5%",
    "net_contents": "750 ml",
    "government_warning": "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink alcoholic beverages during pregnancy because of the risk of birth defects. (2) Consumption of alcoholic beverages impairs your ability to drive a car or operate machinery, and may cause health problems."
  }
]'

curl -sS -X POST "$BASE_URL/verify/batch" \
  -F "images=@label-1.jpg;type=image/jpeg" \
  -F "images=@label-2.jpg;type=image/jpeg" \
  -F "application_data=$BATCH_JSON"
```

Successful single-label response shape:

```json
{
  "overall_verdict": "APPROVED",
  "summary": "All fields matched.",
  "failed_fields": null,
  "latency_ms": 2140.5,
  "field_results": [
    {
      "field": "brand",
      "expected": "Cedar Ridge Smoke Test",
      "found": "Cedar Ridge Smoke Test",
      "status": "PASS",
      "score": 100.0,
      "message": "Brand matches"
    }
  ]
}
```

Successful batch response shape:

```json
{
  "summary": {
    "total": 2,
    "passed": 2,
    "needs_review": 0,
    "errors": 0
  },
  "results": [
    {
      "index": 0,
      "filename": "label-1.jpg",
      "status": "APPROVED",
      "result": {
        "overall_verdict": "APPROVED",
        "field_results": []
      },
      "error": null
    }
  ],
  "latency_ms": 3820.2
}
```

4xx validation error shape:

```json
{
  "detail": {
    "message": "Please fix: Alcohol %.",
    "field_errors": [
      {
        "field": "abv",
        "label": "Alcohol %",
        "message": "Value error, Field must be text"
      }
    ]
  }
}
```

Provider/read failure shape:

```json
{
  "detail": "We could not read this photo. Please try again with a clear label photo."
}
```

## Deployment

Railway is configured by `railway.toml` with this start command:

```bash
sh -c 'cd backend && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}'
```

Set Railway environment variables:

```bash
USE_MOCK_VISION=false
OPENAI_API_KEY=<set in Railway environment only>
PREPROCESS_MAX_DIMENSION=1024
PREPROCESS_JPEG_QUALITY=70
PREPROCESS_GRAYSCALE=true
PREPROCESS_THRESHOLD=off
PREPROCESS_CONTRAST=true
BATCH_CONCURRENCY=3
```

Do not commit `OPENAI_API_KEY`; configure it only in Railway or a local untracked `.env` file.

## Tradeoffs

- Batch uploads are capped at 5 labels (`MAX_BATCH_SIZE = 5`) to keep memory use, OpenAI calls, and free-tier Railway latency bounded. This is below larger stakeholder batch scenarios and should be raised only with queueing, stronger timeout handling, and rate-limit controls.
- Fuzzy matching is deliberately strict at 90% for brand, class/type, and producer. This matches the brief but may send more labels to reviewer override when OCR has small errors.
- ABV tolerance is ±0.1%. This matches the brief and catches small proof/percentage discrepancies, but it can create review work when label OCR drops a decimal.
- Uploaded images are processed in memory and not stored. That keeps the POC stateless and simple, but means there is no reviewer audit trail of original images.

## Secret Handling

- `.env` is listed in `.gitignore`.
- `.env.example` currently uses placeholder values only and does not contain a real API key.
- `git log --all -p -S "AIza"` returned no matches on July 12, 2026.
- `git log --all -p -S "sk-"` did find historical OpenAI-key-shaped material in old `.env.example` commits. This means the repository cannot honestly claim a clean secret-history audit yet. Rotate any exposed key and rewrite/purge Git history before public submission.

## Assumptions

- The app is stateless and uses no database.
- Batch uploads are limited to 5 labels.
- Final submission/demo mode uses the real OpenAI vision provider.
- Real vision extraction depends on configured OpenAI credentials and quota.

## Limitations

- This is a proof of concept, not a production compliance system.
- MockVision validates the application flow for local development but does not perform real OCR/vision.
- Real extraction quality depends on image clarity, lighting, orientation, and model availability.
- Uploaded images are processed in memory and are not stored.
