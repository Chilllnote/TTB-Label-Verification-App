# TTB Label Verification App

This repository contains a minimal FastAPI backend and a simple frontend page for Phase 0.

## What is included

- `backend/app/main.py` — FastAPI app with `/health` endpoint
- `frontend/index.html` — simple page that calls `/health`
- `.env.example` — placeholder environment variables
- `.gitignore` — ignores `.env`, virtualenvs, Python caches, and editor files
- `backend/requirements.txt` — dependency list for deployment
- `Procfile` — optional host start command

## Local development

1. Create a virtual environment:

```bash
cd /mnt/c/Users/donov/Documents/FedStack/TTB-Label-Verification-App/backend
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run the app:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

4. Open the frontend:

- Visit `http://127.0.0.1:8000/`
- The page automatically fetches `/health`

## Deploying to a free tier

### Render

1. Push this repository to GitHub.
2. Create a new Web Service on Render.
3. Connect the GitHub repo.
4. Set the build command to:

```bash
pip install -r backend/requirements.txt
```

5. Set the start command to:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

6. In Render environment settings, add any required env vars such as `API_KEY`.
7. Configure the health check path to `/health`.

### Railway

1. Push this repository to GitHub.
2. Create a new project and deploy from GitHub.
3. Set the start command to:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

4. Railway will automatically install dependencies from `backend/requirements.txt`.
5. Add required env vars in Railway settings.

## Verification

- `http://127.0.0.1:8000/` should load the frontend page
- `http://127.0.0.1:8000/health` should return `{"status":"ok"}`
- The page should show the backend health response after load

## Notes

- Keep `.env` local and never commit secrets.
- Use `.env.example` for placeholder values.
