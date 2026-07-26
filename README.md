# TTB Label Verification App

Proof-of-concept web app for matching alcohol label images against submitted TTB-style application data. The prototype demonstrates the matching workflow without a database: application packages are held in memory, and sample application/image records are saved in the repo to stand in for records that would normally be pulled from production storage.

## Live Demo

- App: `https://ttb-label-verification-app-v2-production.up.railway.app`

The submission deployment is configured to run the real OpenAI vision service with `USE_MOCK_VISION=false`. A valid `OPENAI_API_KEY` must be present in Railway environment variables for `/verify` to return successful real-provider results.

## What It Does

- Shows an application table with saved prototype records.
- Opens a detailed application view with the label image, submitted application data, matching result, and field-level differences.
- Uploads a single application package, or uploads a batch JSON file referencing images already known to the backend.
- Marks uploaded applications as `PENDING` while AI matching is running, then updates them to `ACCEPTED`, `NEEDS_CHECK`, `REJECTED`, or `ERROR`.
- Lets a human reviewer change an application status to accepted or rejected from the detailed view.
- Extracts label values through the configured vision service.
- Compares normal fields with fuzzy/normalized matching.
- Compare the government warning with an exact, case-sensitive match.
- Keeps the core task focused on matching label images to application data.

## Prototype Data And Upload Replication

This prototype has no database. Application records live in memory while the FastAPI process is running. The repo contains mock application packages and label images so the upload and review flow can be demonstrated without standing up storage.

Repo-backed data locations:

- Sample JSON upload files: `sample_JSON/`
  - `sample_JSON/singular_upload.json` uploads one JSON-only application package.
  - `sample_JSON/batch_upload_success.json` uploads a two-item batch whose referenced images exist.
  - `sample_JSON/batch_upload_failure.json` demonstrates batch failure handling, including a missing image reference.
- Seeded application table data: `backend/app/mock_applications/applications.json`
- Seeded/demo label images: `backend/app/mock_applications/images/`
- Additional script/demo images: `backend/scripts/sample_label.jpg` and `backend/scripts/sample_image_2.jpg`

JSON-only uploads do not include image bytes. They use `image_filename` to find an image already stored in the repository-backed image store. The lookup checks:

1. Images uploaded during the current app process, held in memory.
2. `backend/app/mock_applications/images/`
3. `backend/scripts/`

Manual single uploads send the selected image file with the application data. The image bytes are kept in memory for the current process so the detail view can show the image, but they are not written to disk and are not saved in a database.

Application-package JSON shape:

```json
{
  "application_id": "APP-B0000001",
  "image_filename": "mock-approved-label.jpg",
  "application_data": {
    "brand_name": "Northstar Reserve",
    "class_type_designation": "Red Wine",
    "alcohol_content": "13.5%",
    "net_contents": "750 ml",
    "bottler_producer_name_address": "Northstar Test Winery, 100 Valley Road, Napa, CA",
    "country_of_origin": "United States",
    "government_health_warning_statement": "WARNING: CONTAINS ALCOHOL"
  }
}
```

`application_id` is optional for single JSON uploads. When supplied, it must look like `APP-1B81036D`. Batch files must be an array of application packages and are capped at 5 applications.

## Approach

The backend is a stateless FastAPI app. It does not persist application records in a database. Since this is a prototype, the goal is to show how the application would work if a production database already stored application data and label images. In production, application data and image metadata would generally be pulled from that database in JSON format; updates could arrive through either batch upload or single upload.

Uploaded images are validated, resized, JPEG-encoded, optionally augmented through grayscale/contrast/threshold preprocessing, and passed to a `VisionService`. The API path is designed around the requirement that single-label matching should complete within 5 seconds. The submission deployment uses `OpenAIVisionService`; `MockVisionService` is available only as a local development convenience when `USE_MOCK_VISION=true`.

The comparison layer is intentionally stricter for the government warning than for other fields:

- Brand, class/type, and producer use fuzzy matching with a 90% threshold.
- Country uses simple synonym normalization.
- If the vision model leaves country or producer blank, the backend conservatively fills them from clear raw label text such as `PRODUCED IN CANADA` or `Produced and Bottled By Lighthouse Vintners Kingston, NY`.
- ABV uses numeric/proof normalization with ±0.1% tolerance; net contents uses unit normalization.
- Government warning must match exactly, including capitalization, punctuation, spacing, and line breaks.
- Vision provider failures return a distinct unreadable-photo error instead of field mismatches.

The frontend is plain HTML/CSS/JavaScript served by FastAPI. It is intentionally simple and readable: the UI could be more complex, but the prototype is designed so a 73-year-old non-technical user can read, navigate, and review applications without needing instructions. That is why the app uses direct HTML and minimally complex components.

## Prototype Assumptions

- **No database persistence:** Application records are in memory, and repo-backed mock applications stand in for production database records.
- **Database-shaped input:** In a real system, application data and image references would usually be pulled from a database as JSON. The prototype supports single and batch uploads to simulate that update path.
- **Repo-backed images for JSON uploads:** JSON-only application uploads reference label image filenames already stored in `backend/app/mock_applications/images/` or `backend/scripts/`.
- **Human review remains required:** AI output should not be singularly trusted. Applications can be accepted, rejected, or marked as needing a quick review, and reviewers can change accepted/rejected status from the detailed view.
- **Matching is the product:** This app is only about matching submitted application data to the label image. It does not manipulate a database, edit extracted AI values, or perform broader workflow management.
- **Limited reviewer action:** The reviewer can observe the application, inspect the image, review field comparisons, and approve or disapprove based on whether the label and application data matched.

## Failure Checks From Zod Forward

The UI performs the first JSON gate with vendored Zod from `frontend/vendor/zod/`, then the backend repeats validation with Pydantic and image checks. The major failure checks are:

1. **Frontend JSON parsing:** The selected single or batch JSON file must parse as valid JSON.
2. **Frontend Zod shape validation:** Single JSON uploads must be an object with `image_filename` and `application_data`. Batch uploads must be an array of the same package shape.
3. **Frontend required fields:** `image_filename` and all seven application fields must be non-blank strings.
4. **Frontend application ID shape:** If `application_id` is supplied, it must match `APP-[0-9A-F]{8}`.
5. **Frontend batch size:** Batch JSON must contain at least 1 and no more than 5 application packages.
6. **Backend multipart/form validation:** Required file/form fields must be present, or the API returns a user-safe 400 response.
7. **Backend JSON parsing:** `application_data`, application package JSON, and batch JSON must decode correctly.
8. **Backend Pydantic validation:** Application data must contain all required text fields; legacy API keys are accepted and normalized to the application-package field names.
9. **Backend application ID validation:** Missing single-upload IDs are generated; supplied IDs must match `APP-[0-9A-F]{8}`.
10. **Backend batch validation:** `/verify/batch` and `/applications/batch` reject batches over 5 items; `/verify/batch` also requires one image per application-data object.
11. **Image lookup validation:** JSON-only uploads fail if `image_filename` cannot be found in the in-memory upload store, `backend/app/mock_applications/images/`, or `backend/scripts/`.
12. **Image content-type validation:** Uploaded images must be an expected image type such as JPEG, PNG, WebP, GIF, BMP, TIFF, HEIC, or HEIF.
13. **Image byte validation:** Empty images fail, and images over 5 MB fail.
14. **Image readability and dimensions:** Pillow must be able to read the image, the image format must be supported, dimensions must be positive, and the image must be 20 million pixels or fewer.
15. **Preprocessing guardrails:** Images are downscaled, optionally converted to grayscale/thresholded, JPEG-encoded, and inspected again when possible.
16. **Vision extraction failure:** Provider or unreadable-photo failures return a distinct "could not read this photo" style error instead of pretending the fields mismatched.
17. **Field comparison failures:** Brand, class/type, and producer fail below the 90% fuzzy threshold; country fails after synonym normalization; ABV fails outside +/-0.1%; net contents fails outside the volume tolerance; government warning fails unless it is an exact, case-sensitive string match.
18. **Application upload atomicity:** If a single JSON upload or any item in an application batch fails verification or image lookup, failed applications are not added to the table. Batch upload returns per-item failure details.

## Tools And Stack

- Python + FastAPI
- Pydantic
- Pillow for image preprocessing
- RapidFuzz for fuzzy field comparison
- OpenAI Python SDK support for real vision extraction
- Plain HTML/CSS/JavaScript frontend
- Railway deployment

<<<<<<< HEAD
The real vision model is not hardcoded in the app. It is configured with `OPENAI_VISION_MODEL` and used by `OpenAIVisionService`.
=======
Exact vision model: `gpt-5.4-nano`, configured by `OPENAI_VISION_MODEL` and used by `OpenAIVisionService`. On July 12, 2026, this model was checked against OpenAI's current model documentation; the GPT-5.4-nano page lists text and image input, text output, Structured Outputs support, and the `gpt-5.4-nano` alias/snapshot family.
>>>>>>> a5c10696e04ff4e1bacf9be1f0014823d7a71508

## Local Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create local environment settings in a project-root `.env` file:

```dotenv
USE_MOCK_VISION=true
OPENAI_VISION_MODEL=gpt-4o-mini
OPENAI_TIMEOUT_SECONDS=5
OPENAI_IMAGE_DETAIL=high
PREPROCESS_MAX_DIMENSION=1024
PREPROCESS_JPEG_QUALITY=70
PREPROCESS_GRAYSCALE=true
PREPROCESS_THRESHOLD=off
PREPROCESS_CONTRAST=true
BATCH_CONCURRENCY=3
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
<<<<<<< HEAD
| `OPENAI_VISION_MODEL` | Vision model for real OpenAI mode. | required for real mode |
| `OPENAI_TIMEOUT_SECONDS` | OpenAI request timeout. | required for real mode |
| `OPENAI_IMAGE_DETAIL` | OpenAI image detail setting. | required for real mode |
| `PREPROCESS_MAX_DIMENSION` | Long-edge image resize target. Images are only downscaled, never upscaled. | required |
| `PREPROCESS_JPEG_QUALITY` | JPEG quality after preprocessing. | required |
| `PREPROCESS_GRAYSCALE` | Convert image to grayscale before sending to vision. | required |
| `PREPROCESS_THRESHOLD` | Optional black/white threshold mode: `off`, `binary`, or `adaptive`. | required |
| `PREPROCESS_CONTRAST` | Apply light contrast/sharpening before threshold-capable preprocessing. | required |
| `BATCH_CONCURRENCY` | Max concurrent batch item checks. | required |
=======
| `OPENAI_VISION_MODEL` | Vision model for real OpenAI mode. | `gpt-5.4-nano` |
| `OPENAI_TIMEOUT_SECONDS` | OpenAI request timeout. | `20` |
| `OPENAI_IMAGE_DETAIL` | OpenAI image detail setting. | `high` |
| `PREPROCESS_MAX_DIMENSION` | Long-edge image resize target. Images are only downscaled, never upscaled. | `1024` |
| `PREPROCESS_JPEG_QUALITY` | JPEG quality after preprocessing. | `70` |
| `PREPROCESS_GRAYSCALE` | Convert image to grayscale before sending to vision. | `true` |
| `PREPROCESS_THRESHOLD` | Optional black/white threshold mode: `off`, `binary`, or `adaptive`. | `off` |
| `PREPROCESS_CONTRAST` | Apply light contrast/sharpening before threshold-capable preprocessing. | `true` |
| `BATCH_CONCURRENCY` | Max concurrent batch item checks. | `3` |
>>>>>>> a5c10696e04ff4e1bacf9be1f0014823d7a71508

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
.venv/bin/python scripts/benchmark_live.py https://ttb-label-verification-app-production-ec48.up.railway.app 5 30
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

The current Railway app is reachable (`/health` returns `{"status":"ok"}`), but `/verify` returned `503` with `We could not read this photo. Please try again with a clear label photo.` for every benchmark attempt. That means the successful real-provider p50/p95 required for final review still needs to be rerun after Railway has a valid `OPENAI_API_KEY`, quota, and provider access.

Railway cold-start note: the first request after inactivity may include container startup time. Treat the first benchmark sample separately when collecting final p50/p95 numbers for the 5-second target.

## API Examples

The primary UI workflow uses application-package endpoints such as `/applications`, `/applications/upload`, `/applications/upload-json`, `/applications/batch`, and `/applications/{application_id}/status`. The lower-level `/verify` and `/verify/batch` endpoints remain available for direct matching checks and regression scripts.

Set the deployed base URL:

```bash
BASE_URL="https://ttb-label-verification-app-production-ec48.up.railway.app"
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

- Batch uploads are capped at 5 applications (`MAX_BATCH_SIZE = 5`) to keep memory use, OpenAI calls, and free-tier Railway latency bounded. This is below larger stakeholder batch scenarios and should be raised only with queueing, stronger timeout handling, and rate-limit controls.
- Fuzzy matching is deliberately strict at 90% for brand, class/type, and producer. This matches the brief but may send more labels to reviewer override when OCR has small errors.
- ABV tolerance is ±0.1%. This matches the brief and catches small proof/percentage discrepancies, but it can create review work when label OCR drops a decimal.
- Uploaded images are processed in memory and are not persisted to a database. That keeps the POC stateless and simple, but means there is no durable reviewer audit trail of original uploaded images.

## Secret Handling

- `.env` is listed in `.gitignore`.
- The repo should not include real `.env` values or API keys.
- `git log --all -p -S "AIza"` returned no matches on July 12, 2026.
- `git log --all -p -S "sk-"` did find historical OpenAI-key-shaped material in old `.env.example` commits. This means the repository cannot honestly claim a clean secret-history audit yet. Rotate any exposed key and rewrite/purge Git history before public submission.

## Assumptions

- The app is stateless and uses no database; repo-backed sample records stand in for database records.
- Batch uploads are limited to 5 applications.
- Final submission/demo mode uses the real OpenAI vision provider.
- Real vision extraction depends on configured OpenAI credentials and quota.
- Human review is part of the expected workflow, so status can be changed to accepted or rejected from the detail view.
- The product scope is matching only: observe the application, inspect the image, review the match, and approve or disapprove.

## Limitations

- This is a proof of concept, not a production compliance system.
- MockVision validates the application flow for local development but does not perform real OCR/vision.
- Real extraction quality depends on image clarity, lighting, orientation, and model availability.
- Uploaded images are processed in memory and are not stored.
