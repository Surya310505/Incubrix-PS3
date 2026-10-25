import asyncio
import uuid
import yaml
import pytest
from httpx import ASGITransport, AsyncClient
from fastapi.testclient import TestClient
from app import main


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["X-Correlation-Id"]


def test_ready(client):
    response = client.get("/ready")
    assert response.status_code == 200


def test_file_route_streams_from_configured_object_storage(client, monkeypatch):
    job_id = str(uuid.uuid4())

    class FakeObjectStorage:
        def read_object(self, requested_job_id, filename):
            assert requested_job_id == job_id
            assert filename == "video-qa.json"
            return iter([b'{"status":"passed"}']), 19

    monkeypatch.setattr(main, "create_object_storage", FakeObjectStorage)
    response = client.get(f"/api/v1/jobs/{job_id}/files/video-qa.json")

    assert response.status_code == 200
    assert response.json() == {"status": "passed"}
    assert response.headers["content-length"] == "19"


def test_api_docs_are_versioned(client):
    response = client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    assert response.json()["openapi"] == "3.1.0"


def test_unknown_route_uses_standard_error_envelope(client):
    response = client.get("/api/v1/not-a-route")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "HTTP_ERROR"
    assert set(response.json()["error"]) == {"code", "message", "retryable", "details"}


def test_openapi_documents_every_registered_route():
    spec = yaml.safe_load((main.PROJECT_ROOT / "openapi.yaml").read_text(encoding="utf-8"))
    actual = {
        route.path: set(route.methods)
        for route in main.app.routes
        if getattr(route, "methods", None)
    }
    documented = {
        path: {method.upper() for method in operations if method.lower() in {"get", "head", "post"}}
        for path, operations in spec["paths"].items()
    }
    assert documented == actual


def test_concurrent_submissions_do_not_duplicate_idempotent_jobs(monkeypatch):
    processed = []

    async def record_job(job_id, payload):
        processed.append(job_id)

    monkeypatch.setattr(main, "process_job", record_job)
    main.init_db()
    main.app.state.job_tasks = set()
    repeated_key = f"concurrent-{uuid.uuid4()}"

    async def submit_simultaneously():
        transport = ASGITransport(app=main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {"script": "Concurrent idempotency check."}
            requests = await asyncio.gather(
                client.post("/api/v1/broll/jobs", headers={"Idempotency-Key": repeated_key}, json=payload),
                client.post("/api/v1/broll/jobs", headers={"Idempotency-Key": repeated_key}, json=payload),
                client.post(
                    "/api/v1/broll/jobs",
                    headers={"Idempotency-Key": f"independent-{uuid.uuid4()}"},
                    json={"script": "Independent concurrent job."},
                ),
            )
            await asyncio.sleep(0.05)
            return requests

    responses = asyncio.run(submit_simultaneously())
    assert all(response.status_code == 202 for response in responses)
    assert responses[0].json()["job_id"] == responses[1].json()["job_id"]
    assert responses[2].json()["job_id"] != responses[0].json()["job_id"]
    assert len(processed) == 2


def test_job_requires_idempotency_key(client):
    response = client.post("/api/v1/broll/jobs", json={"script": "Saving money matters."})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_scene_list_with_visual_hint_is_accepted(client, monkeypatch):
    async def no_op(job_id, payload):
        return None

    monkeypatch.setattr(main, "process_job", no_op)
    response = client.post(
        "/api/v1/broll/jobs",
        headers={"Idempotency-Key": f"scene-list-{uuid.uuid4()}"},
        json={
            "scenes": [
                {
                    "text": "A family saves toward a home.",
                    "visual_hint": "Warm abstract house and savings imagery.",
                }
            ]
        },
    )
    assert response.status_code == 202
    assert response.json()["status"] in {"queued", "running"}


def test_invalid_aspect_ratio_rejected(client):
    response = client.post(
        "/api/v1/broll/jobs",
        headers={"Idempotency-Key": "test-invalid-ratio"},
        json={"script": "Saving money matters.", "aspect_ratios": ["4:3"]},
    )
    assert response.status_code == 422
    assert set(response.json()["error"]) == {"code", "message", "retryable", "details"}


def test_audio_url_is_queued_and_left_for_the_background_pipeline(client, monkeypatch):
    async def no_op(job_id, payload):
        return None

    monkeypatch.setattr(main, "process_job", no_op)
    response = client.post(
        "/api/v1/broll/jobs",
        headers={"Idempotency-Key": f"audio-{uuid.uuid4()}"},
        json={"audio_url": "https://media.example.test/audio.mp3"},
    )
    assert response.status_code == 202
    assert response.json()["status_url"].startswith("/api/v1/jobs/")


def test_audio_upload_is_stored_and_idempotent(client, monkeypatch):
    async def no_op(job_id, payload):
        return None

    monkeypatch.setattr(main, "process_job", no_op)
    key = f"upload-{uuid.uuid4()}"
    first = client.post(
        "/api/v1/broll/jobs/audio",
        headers={"Idempotency-Key": key},
        files={"audio_file": ("voice.wav", b"wav-data", "audio/wav")},
    )
    second = client.post(
        "/api/v1/broll/jobs/audio",
        headers={"Idempotency-Key": key},
        files={"audio_file": ("voice.wav", b"wav-data", "audio/wav")},
    )
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert second.json()["idempotent_replay"] is True
    stored = client.get(first.json()["status_url"]).json()
    assert stored["status"] in {"queued", "running"}
    assert (main.OUTPUT_DIR / stored["job_id"] / "input.wav").read_bytes() == b"wav-data"


def test_audio_upload_rejects_mismatched_content_type(client):
    response = client.post(
        "/api/v1/broll/jobs/audio",
        headers={"Idempotency-Key": f"upload-type-{uuid.uuid4()}"},
        files={"audio_file": ("voice.wav", b"audio", "audio/mpeg")},
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_AUDIO_FORMAT"


def test_audio_upload_enforces_byte_limit(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_AUDIO_UPLOAD_BYTES", 4)
    response = client.post(
        "/api/v1/broll/jobs/audio",
        headers={"Idempotency-Key": f"upload-size-{uuid.uuid4()}"},
        files={"audio_file": ("voice.wav", b"12345", "audio/wav")},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "AUDIO_TOO_LARGE"


def test_same_idempotency_key_returns_same_job(client, monkeypatch):
    async def no_op(job_id, payload):
        return None

    monkeypatch.setattr(main, "process_job", no_op)
    key = f"retry-{uuid.uuid4()}"
    payload = {"script": "A simple test input."}
    first = client.post("/api/v1/broll/jobs", headers={"Idempotency-Key": key}, json=payload)
    second = client.post("/api/v1/broll/jobs", headers={"Idempotency-Key": key}, json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]


def test_idempotency_key_cannot_be_reused_for_different_payload(client, monkeypatch):
    async def no_op(job_id, payload):
        return None

    monkeypatch.setattr(main, "process_job", no_op)
    key = f"conflict-{uuid.uuid4()}"
    first = client.post(
        "/api/v1/broll/jobs",
        headers={"Idempotency-Key": key},
        json={"script": "First request."},
    )
    second = client.post(
        "/api/v1/broll/jobs",
        headers={"Idempotency-Key": key},
        json={"script": "Different request."},
    )
    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
