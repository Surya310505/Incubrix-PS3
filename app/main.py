from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, HttpUrl, ValidationError, model_validator
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.pipeline import render_script_job
from app.storage import ObjectNotFoundError, create_object_storage

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("DATA_DIR", str(PROJECT_ROOT / "data")))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(DATA_DIR / "outputs")))
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "jobs.sqlite3")))
MAX_AUDIO_SECONDS = int(os.getenv("MAX_AUDIO_SECONDS", "600"))
MAX_AUDIO_UPLOAD_BYTES = int(os.getenv("MAX_AUDIO_UPLOAD_BYTES", str(100 * 1024 * 1024)))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
MAX_SCRIPT_CHARACTERS = int(os.getenv("MAX_SCRIPT_CHARACTERS", "30000"))
MAX_SCENES = int(os.getenv("MAX_SCENES", "100"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=LOG_LEVEL, format="%(message)s")
logger = logging.getLogger("incubrix_ps3")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect_db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            idempotency_key TEXT UNIQUE,
            request_json TEXT NOT NULL,
            status TEXT NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0,
            result_json TEXT,
            error_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)
        conn.commit()


class SceneInput(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    visual_hint: str | None = Field(default=None, max_length=1000)


class CaptionsInput(BaseModel):
    burn_in: bool = True
    srt: bool = True


class JobOptions(BaseModel):
    language: str | None = Field(default=None, min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")
    voice: str = "en-IN-female"
    aspect_ratios: list[str] = Field(default_factory=lambda: ["16:9"])
    style: str = "abstract motion graphics (offline fallback)"
    avoid: list[str] = Field(default_factory=list)
    captions: CaptionsInput = Field(default_factory=CaptionsInput)

    @model_validator(mode="after")
    def validate_options(self):
        if not self.aspect_ratios:
            raise ValueError("At least one aspect ratio is required.")
        invalid = sorted(set(self.aspect_ratios) - {"16:9", "9:16", "1:1"})
        if invalid:
            raise ValueError(f"Unsupported aspect ratio(s): {', '.join(invalid)}")
        return self


class JobRequest(JobOptions):
    audio_url: HttpUrl | None = None
    script: str | None = Field(default=None, max_length=MAX_SCRIPT_CHARACTERS)
    scenes: list[SceneInput] | None = Field(default=None, max_length=MAX_SCENES)

    @model_validator(mode="after")
    def validate_input(self):
        if not self.audio_url and not (self.script and self.script.strip()) and not self.scenes:
            raise ValueError("Provide one of audio_url, non-empty script, or scenes.")
        if self.audio_url and self.audio_url.scheme != "https":
            raise ValueError("Audio URLs must use HTTPS.")
        if self.audio_url and (self.script or self.scenes):
            raise ValueError("Provide only one input mode: audio_url, script, or scenes.")
        if not self.audio_url and self.scenes and self.script:
            raise ValueError("Provide either script or scenes, not both.")
        return self


def json_log(level: str, message: str, **fields: Any) -> None:
    record = {"timestamp": utc_now(), "level": level, "message": message, **fields}
    getattr(logger, level.lower(), logger.info)(json.dumps(record, ensure_ascii=False))


def error_response(code: str, message: str, retryable: bool, details: Any = None) -> dict:
    return {"error": {"code": code, "message": message, "retryable": retryable, "details": details}}


def _request_identity(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "audio_path"}


def _enqueue_job(
    payload: dict[str, Any],
    idempotency_key: str,
    request: Request,
    staged_upload: Path | None = None,
    upload_extension: str | None = None,
) -> dict[str, Any]:
    destination: Path | None = None
    try:
        with connect_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM jobs WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                previous_payload = json.loads(existing["request_json"])
                if _request_identity(previous_payload) != _request_identity(payload):
                    raise HTTPException(
                        status_code=409,
                        detail=error_response(
                            "IDEMPOTENCY_KEY_REUSED",
                            "This Idempotency-Key was already used with a different request.",
                            False,
                            None,
                        ),
                    )
                return {
                    "job_id": existing["job_id"],
                    "status": existing["status"],
                    "progress": existing["progress"],
                    "status_url": f"/api/v1/jobs/{existing['job_id']}",
                    "idempotent_replay": True,
                }

            job_id = str(uuid.uuid4())
            stored_payload = dict(payload)
            if staged_upload and upload_extension:
                job_dir = OUTPUT_DIR / job_id
                job_dir.mkdir(parents=True, exist_ok=False)
                destination = job_dir / f"input{upload_extension}"
                os.replace(staged_upload, destination)
                stored_payload["audio_path"] = str(destination)
            now = utc_now()
            conn.execute(
                "INSERT INTO jobs(job_id,idempotency_key,request_json,status,progress,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    job_id,
                    idempotency_key,
                    json.dumps(stored_payload),
                    "queued",
                    0,
                    now,
                    now,
                ),
            )
            conn.commit()
    except Exception:
        if destination:
            destination.unlink(missing_ok=True)
        raise
    finally:
        if staged_upload:
            staged_upload.unlink(missing_ok=True)

    schedule_job(job_id, stored_payload)
    json_log(
        "INFO",
        "job_queued",
        job_id=job_id,
        correlation_id=getattr(request.state, "correlation_id", None),
    )
    return {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "status_url": f"/api/v1/jobs/{job_id}",
    }


async def _stage_audio_upload(audio_file: UploadFile) -> tuple[Path, str, str]:
    filename = Path(audio_file.filename or "").name
    extension = Path(filename).suffix.lower()
    content_types = {
        ".mp3": {"audio/mpeg", "audio/mp3", "application/octet-stream"},
        ".wav": {"audio/wav", "audio/x-wav", "audio/wave", "application/octet-stream"},
        ".m4a": {"audio/mp4", "audio/x-m4a", "application/octet-stream"},
    }
    if extension not in content_types:
        await audio_file.close()
        raise HTTPException(
            status_code=415,
            detail=error_response(
                "UNSUPPORTED_AUDIO_FORMAT",
                "Upload an MP3, WAV, or M4A audio file.",
                False,
                None,
            ),
        )
    content_type = (audio_file.content_type or "").lower()
    if content_type and content_type not in content_types[extension]:
        await audio_file.close()
        raise HTTPException(
            status_code=415,
            detail=error_response(
                "UNSUPPORTED_AUDIO_FORMAT",
                "Uploaded audio content type does not match its file extension.",
                False,
                None,
            ),
        )
    staging_dir = DATA_DIR / "upload-staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    descriptor, filename_path = tempfile.mkstemp(
        prefix="audio-",
        suffix=extension,
        dir=staging_dir,
    )
    staged_path = Path(filename_path)
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(descriptor, "wb") as destination:
            while chunk := await audio_file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_AUDIO_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=error_response(
                            "AUDIO_TOO_LARGE",
                            f"Audio upload exceeds the {MAX_AUDIO_UPLOAD_BYTES}-byte limit.",
                            False,
                            None,
                        ),
                    )
                digest.update(chunk)
                destination.write(chunk)
        if total == 0:
            raise HTTPException(
                status_code=400,
                detail=error_response("EMPTY_AUDIO", "Uploaded audio file is empty.", False, None),
            )
        return staged_path, extension, digest.hexdigest()
    except Exception:
        staged_path.unlink(missing_ok=True)
        raise
    finally:
        await audio_file.close()


def _parse_form_boolean(value: str | None, field: str) -> bool:
    if value is None:
        return True
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise HTTPException(
        status_code=422,
        detail=error_response(
            "VALIDATION_ERROR",
            f"{field} must be true or false.",
            False,
            None,
        ),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    storage = create_object_storage()
    if storage:
        await asyncio.to_thread(storage.ensure_bucket)
    app.state.job_tasks = set()
    with connect_db() as conn:
        interrupted = conn.execute(
            "SELECT job_id, request_json FROM jobs WHERE status IN ('queued', 'running')"
        ).fetchall()
        conn.execute(
            "UPDATE jobs SET status='queued', updated_at=? WHERE status='running'",
            (utc_now(),),
        )
    for row in interrupted:
        schedule_job(row["job_id"], json.loads(row["request_json"]))
    yield


app = FastAPI(
    title="IncuBrix Script and Voiceover to B-roll Engine",
    version="0.1.0",
    docs_url="/api/v1/docs",
    swagger_ui_oauth2_redirect_url="/api/v1/docs/oauth2-redirect",
    redoc_url=None,
    openapi_url="/api/v1/openapi.json",
    lifespan=lifespan,
)


def schedule_job(job_id: str, payload: dict[str, Any]) -> None:
    task = asyncio.create_task(process_job(job_id, payload))
    app.state.job_tasks.add(task)
    task.add_done_callback(app.state.job_tasks.discard)


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
    request.state.correlation_id = correlation_id
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Correlation-Id"] = correlation_id
    json_log("INFO", "http_request", correlation_id=correlation_id,
             job_id=request.path_params.get("job_id"),
             method=request.method, path=request.url.path,
             status_code=response.status_code,
             duration_ms=round((time.perf_counter() - start) * 1000, 2))
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        body = detail
    else:
        body = error_response("HTTP_ERROR", str(detail), False, None)
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(Exception)
async def unexpected_exception_handler(request: Request, exc: Exception):
    json_log("ERROR", "unhandled_exception",
             correlation_id=getattr(request.state, "correlation_id", None),
             job_id=request.path_params.get("job_id"),
             exception_type=type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content=error_response("INTERNAL_ERROR", "An unexpected error occurred.", True, None),
    )


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = [{"location": error["loc"], "message": error["msg"]} for error in exc.errors()]
    return JSONResponse(
        status_code=422,
        content=error_response("VALIDATION_ERROR", "Request validation failed.", False, errors),
    )


@app.get("/health")
@app.get("/api/v1/health")
def health():
    return {"status": "ok", "service": "incubrix-ps3"}


@app.get("/ready")
@app.get("/api/v1/ready")
def ready():
    try:
        with connect_db() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ready"}
    except Exception:
        raise HTTPException(status_code=503, detail=error_response(
            "NOT_READY", "Database is not available.", True, None
        ))


async def process_job(job_id: str, payload: dict[str, Any]) -> None:
    with connect_db() as conn:
        conn.execute("UPDATE jobs SET status='running', progress=5, updated_at=? WHERE job_id=?",
                     (utc_now(), job_id))
        conn.commit()

    try:
        with connect_db() as conn:
            conn.execute("UPDATE jobs SET progress=20, updated_at=? WHERE job_id=?",
                         (utc_now(), job_id))
            conn.commit()
        result = await asyncio.to_thread(
            render_script_job, job_id, payload, OUTPUT_DIR / job_id
        )
        storage = create_object_storage()
        if storage:
            await asyncio.to_thread(
                storage.upload_job_outputs,
                job_id,
                OUTPUT_DIR / job_id,
                list(result["files"]),
            )
        with connect_db() as conn:
            conn.execute("UPDATE jobs SET status='succeeded', progress=100, result_json=?, updated_at=? WHERE job_id=?",
                         (json.dumps(result), utc_now(), job_id))
            conn.commit()
        json_log("INFO", "job_finished", job_id=job_id)
    except Exception as exc:
        shutil.rmtree(OUTPUT_DIR / job_id / "work", ignore_errors=True)
        error = error_response("PROCESSING_FAILED", str(exc), False, {"stage": "pipeline"})
        with connect_db() as conn:
            conn.execute("UPDATE jobs SET status='failed', progress=100, error_json=?, updated_at=? WHERE job_id=?",
                         (json.dumps(error), utc_now(), job_id))
            conn.commit()
        json_log("ERROR", "job_failed", job_id=job_id, exception_type=type(exc).__name__)


@app.post("/api/v1/broll/jobs", status_code=202)
async def create_job(
    body: JobRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail=error_response(
            "IDEMPOTENCY_KEY_REQUIRED", "Send an Idempotency-Key header.", False, None
        ))
    payload = body.model_dump(mode="json")
    return _enqueue_job(payload, idempotency_key, request)


@app.post("/api/v1/broll/jobs/audio", status_code=202)
async def create_audio_upload_job(
    request: Request,
    audio_file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    language: str | None = Form(default=None),
    aspect_ratios: list[str] = Form(default=["16:9"]),
    style: str = Form(default="abstract motion graphics (offline fallback)"),
    avoid: list[str] = Form(default=[]),
    captions_burn_in: str | None = Form(default=None),
    captions_srt: str | None = Form(default=None),
):
    if not idempotency_key:
        await audio_file.close()
        raise HTTPException(
            status_code=400,
            detail=error_response(
                "IDEMPOTENCY_KEY_REQUIRED",
                "Send an Idempotency-Key header.",
                False,
                None,
            ),
        )
    try:
        options = JobOptions(
            language=language or None,
            aspect_ratios=aspect_ratios,
            style=style,
            avoid=avoid,
            captions={
                "burn_in": _parse_form_boolean(captions_burn_in, "captions_burn_in"),
                "srt": _parse_form_boolean(captions_srt, "captions_srt"),
            },
        )
    except ValidationError as exc:
        await audio_file.close()
        raise HTTPException(
            status_code=422,
            detail=error_response(
                "VALIDATION_ERROR",
                "Audio job settings are invalid.",
                False,
                {"reason": str(exc)},
            ),
        ) from exc
    staged_path, extension, audio_hash = await _stage_audio_upload(audio_file)
    payload = options.model_dump(mode="json")
    payload.update(
        {
            "audio_upload_sha256": audio_hash,
            "audio_extension": extension,
        }
    )
    return _enqueue_job(
        payload,
        idempotency_key,
        request,
        staged_upload=staged_path,
        upload_extension=extension,
    )


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str):
    with connect_db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=error_response("JOB_NOT_FOUND", "Job was not found.", False, {"job_id": job_id}))
    result = json.loads(row["result_json"]) if row["result_json"] else None
    error = json.loads(row["error_json"]) if row["error_json"] else None
    return {
        "job_id": row["job_id"], "status": row["status"], "progress": row["progress"],
        "result": result, "error": error, "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


@app.get("/api/v1/jobs/{job_id}/files/{filename}")
def get_job_file(job_id: str, filename: str):
    allowed = {
        "timeline.json", "manifest.json", "video-qa.json", "captions.srt",
        "video_16_9.mp4", "video_9_16.mp4", "video_1_1.mp4",
    }
    if filename not in allowed:
        raise HTTPException(status_code=404, detail=error_response("FILE_NOT_FOUND", "File was not found.", False, None))
    try:
        canonical_job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=404, detail=error_response("FILE_NOT_FOUND", "File was not found.", False, None))
    path = OUTPUT_DIR / canonical_job_id / filename
    media_type = {
        "timeline.json": "application/json",
        "manifest.json": "application/json",
        "video-qa.json": "application/json",
        "captions.srt": "application/x-subrip",
        "video_16_9.mp4": "video/mp4",
        "video_9_16.mp4": "video/mp4",
        "video_1_1.mp4": "video/mp4",
    }[filename]
    storage = create_object_storage()
    if storage:
        try:
            chunks, content_length = storage.read_object(canonical_job_id, filename)
        except ObjectNotFoundError:
            raise HTTPException(
                status_code=404,
                detail=error_response(
                    "FILE_NOT_FOUND",
                    "File was not found in object storage.",
                    False,
                    {"filename": filename},
                ),
            )
        return StreamingResponse(
            chunks,
            media_type=media_type,
            headers={"Content-Length": str(content_length)},
        )
    if not path.is_file():
        raise HTTPException(status_code=404, detail=error_response("FILE_NOT_FOUND", "File was not found.", False, {"filename": filename}))
    return FileResponse(path, media_type=media_type)
