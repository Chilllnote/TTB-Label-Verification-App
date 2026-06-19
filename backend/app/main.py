from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="TTB Label Verification App")

frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="frontend")

@app.get("/")
async def root():
    return FileResponse(frontend_dir / "index.html")

@app.get("/health")
async def health():
    return JSONResponse(status_code=200, content={"status": "ok"})
