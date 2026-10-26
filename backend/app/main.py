from __future__ import annotations

import logging
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .config import settings
from .schemas import FactCheckRequest, ImageUrlRequest, TextRequest, UrlRequest, VideoUrlRequest
from .services.factcheck import factcheck_claim
from .services.image_analysis import analyze_image, analyze_image_url
from .services.model_registry import model_status
from .services.mongolian import detect_language
from .services.net_utils import UnsafeUrlError
from .services.text_analysis import analyze_text
from .services.url_scan import scan_url
from .services.video_analysis import analyze_video
from .services.video_url_fetch import VideoFetchError, fetch_video_url

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("tayo")

app = FastAPI(
    title="TAYO API",
    version="0.1.0",
    description=(
        "Local-first fact-checking and synthetic-media triage API for journalists. "
        "Every model output is a signal, not proof."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

IMAGE_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/bmp",
    "image/tiff",
    "image/avif",
}
VIDEO_CONTENT_TYPES = {
    "video/mp4",
    "video/webm",
    "video/quicktime",
    "video/x-matroska",
    "video/x-msvideo",
    "video/mpeg",
    "video/ogg",
    "application/octet-stream",
}


@app.exception_handler(UnsafeUrlError)
async def unsafe_url_handler(_: Request, exc: UnsafeUrlError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc), "error": "unsafe_url"},
    )


@app.get("/")
async def root() -> dict:
    return {
        "name": "TAYO API",
        "version": app.version,
        "docs": "/docs",
        "health": "/api/health",
        "note": "Local demo. Results are triage signals, not proof.",
    }


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "version": app.version,
        "uptime_seconds": round(time.monotonic() - STARTED, 1),
        "models": model_status(),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


STARTED = time.monotonic()


@app.get("/api/config")
async def config() -> dict:
    """Public, non-secret runtime information for the extension UI."""
    return {
        "max_upload_mb": settings.max_upload_mb,
        "max_image_url_mb": settings.max_image_url_mb,
        "video_frames": settings.video_frames,
        "device": model_status()["device"],
        "features": {
            "factcheck_corpus": True,
            "google_factcheck": bool(settings.google_factcheck_api_key),
            "ollama_summary": bool(settings.ollama_url),
            "image_model": True,
            "text_model": settings.enable_text_model,
            "video": True,
            "url_scan": True,
            "safe_browsing": bool(settings.google_safe_browsing_api_key),
            "virustotal": bool(settings.virustotal_api_key),
            "urlhaus": bool(settings.urlhaus_auth_key),
        },
        "models": {
            "image_primary": settings.image_model_primary,
            "image_fallback": settings.image_model_fallback,
            "image_secondary": (
                settings.image_model_secondary
                if settings.enable_secondary_image_model
                else None
            ),
            "text": settings.text_model_id if settings.enable_text_model else None,
            "smaug_crops": settings.smaug_crops,
        },
        "limitations": [
            "AI detectors are probabilistic and can be fooled or produce false positives.",
            "The curated fact-check corpus is small; connect the Google Fact Check Tools API for broader coverage.",
            "URL scanning is passive and never fetches the page body or runs JavaScript.",
            "This is a demonstration, not a forensic or editorial decision system.",
        ],
    }


@app.post("/api/factcheck")
async def api_factcheck(payload: FactCheckRequest) -> dict:
    try:
        return await factcheck_claim(payload.claim, payload.language)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("factcheck failed")
        raise HTTPException(status_code=500, detail=f"Fact-check failed: {exc}") from exc


@app.post("/api/analyze/text")
async def api_analyze_text(payload: TextRequest) -> dict:
    try:
        # Mongolian returns lightweight statistics without loading a model.
        language = (
            detect_language(payload.text)
            if payload.language == "auto" else payload.language
        )
        if language in {"mn_cyrl", "mn_latn"}:
            return analyze_text(payload.text, payload.language)
        result = await run_in_threadpool(analyze_text, payload.text, payload.language)
        return result
    except Exception as exc:
        logger.exception("text analysis failed")
        raise HTTPException(status_code=500, detail=f"Text analysis failed: {exc}") from exc


async def _save_upload(upload: UploadFile, max_mb: int) -> tuple[Path, str, int]:
    """Stream an upload to a temporary file while enforcing a size limit."""
    suffix = Path(upload.filename or "").suffix[:10] or ".bin"
    max_bytes = max_mb * 1024 * 1024
    total = 0
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    path = Path(temp.name)
    try:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"File is larger than the {max_mb} MB limit.",
                )
            temp.write(chunk)
    except Exception:
        temp.close()
        path.unlink(missing_ok=True)
        raise
    temp.close()
    if total == 0:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    return path, upload.filename or path.name, total


@app.post("/api/analyze/image")
async def api_analyze_image(
    file: UploadFile = File(...),
    tiles: bool = False,
) -> dict:
    content_type = (file.content_type or "").split(";")[0].lower()
    if content_type and content_type not in IMAGE_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported image content type: {content_type}",
        )
    path, name, _ = await _save_upload(file, settings.max_upload_mb)
    try:
        result = await run_in_threadpool(analyze_image, path, name, None, tiles)
        return result
    except Exception as exc:
        logger.exception("image analysis failed")
        raise HTTPException(status_code=500, detail=f"Image analysis failed: {exc}") from exc
    finally:
        path.unlink(missing_ok=True)


@app.post("/api/analyze/image-url")
async def api_analyze_image_url(payload: ImageUrlRequest) -> dict:
    try:
        return await analyze_image_url(payload.url, tiles=payload.tiles)
    except UnsafeUrlError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("image URL analysis failed")
        raise HTTPException(status_code=502, detail=f"Could not analyze image URL: {exc}") from exc


@app.post("/api/analyze/video")
async def api_analyze_video(file: UploadFile = File(...)) -> dict:
    content_type = (file.content_type or "").split(";")[0].lower()
    if content_type and content_type not in VIDEO_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported video content type: {content_type}",
        )
    path, name, _ = await _save_upload(file, settings.max_upload_mb)
    try:
        # Frames live next to the upload, inside a per-request directory so the
        # TemporaryDirectory cleanup removes both the file and extracted frames.
        with tempfile.TemporaryDirectory(prefix="tayo_video_") as workdir:
            target = Path(workdir) / f"upload{Path(name).suffix[:10] or '.mp4'}"
            path.replace(target)
            result = await run_in_threadpool(analyze_video, target, name)
        return result
    except Exception as exc:
        logger.exception("video analysis failed")
        raise HTTPException(status_code=500, detail=f"Video analysis failed: {exc}") from exc
    finally:
        path.unlink(missing_ok=True)


@app.post("/api/analyze/video-url")
async def api_analyze_video_url(payload: VideoUrlRequest) -> dict:
    try:
        with tempfile.TemporaryDirectory(prefix="tayo_video_url_") as workdir:
            path, final_url, filename = await run_in_threadpool(
                fetch_video_url, payload.url, Path(workdir)
            )
            result = await run_in_threadpool(analyze_video, path, filename)
            result["source_url"] = final_url
            return result
    except UnsafeUrlError:
        raise
    except VideoFetchError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("video URL analysis failed")
        raise HTTPException(status_code=502, detail=f"Could not analyze video URL: {exc}") from exc


@app.post("/api/scan/url")
async def api_scan_url(payload: UrlRequest) -> dict:
    try:
        return await scan_url(payload.url)
    except UnsafeUrlError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("URL scan failed")
        raise HTTPException(status_code=502, detail=f"URL scan failed: {exc}") from exc


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    response = await call_next(request)
    elapsed = (time.perf_counter() - started) * 1000
    response.headers["x-request-id"] = request_id
    response.headers["x-elapsed-ms"] = f"{elapsed:.0f}"
    logger.info(
        "%s %s -> %s (%.0f ms) id=%s",
        request.method,
        request.url.path,
        response.status_code,
        elapsed,
        request_id,
    )
    return response
