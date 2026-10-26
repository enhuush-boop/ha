from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from .services.text_analysis import analyze_text

app = FastAPI(title="TAYO Text", version="1.0.0")
class TextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "TAYO Text"}

@app.post("/api/analyze/text")
async def analyze(payload: TextRequest):
    return await run_in_threadpool(analyze_text, payload.text)

app.mount("/", StaticFiles(directory=Path(__file__).resolve().parent.parent / "web", html=True), name="web")
