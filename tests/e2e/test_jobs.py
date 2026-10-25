import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.pipeline import _tts_audio


@pytest.mark.e2e
def test_http_job_reaches_validated_downloadable_video(tmp_path: Path, monkeypatch):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg/FFprobe are required for the end-to-end test.")
    if not shutil.which("say") and not shutil.which("espeak-ng"):
        pytest.skip("Install macOS say or espeak-ng for the end-to-end test.")

    data_dir = tmp_path / "data"
    output_dir = data_dir / "outputs"
    data_dir.mkdir()
    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(main, "DB_PATH", data_dir / "jobs.sqlite3")

    with TestClient(main.app) as client:
        response = client.post(
            "/api/v1/broll/jobs",
            headers={"Idempotency-Key": f"e2e-{uuid.uuid4()}"},
            json={
                "script": "Create a plan and review it every week.",
                "aspect_ratios": ["16:9"],
                "captions": {"burn_in": True, "srt": True},
            },
        )
        assert response.status_code == 202
        assert response.headers["X-Correlation-Id"]
        accepted = response.json()
        assert accepted["job_id"]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            job = client.get(accepted["status_url"]).json()
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.1)
        assert job["status"] == "succeeded", job.get("error")
        assert job["progress"] == 100
        assert {
            "video_16_9.mp4", "captions.srt", "timeline.json", "manifest.json",
            "video-qa.json",
        } <= set(job["result"]["files"])
        qa_path = output_dir / accepted["job_id"] / "video-qa.json"
        if main.create_object_storage():
            qa_path.unlink()
        qa_response = client.get(job["result"]["files"]["video-qa.json"])
        assert qa_response.status_code == 200
        assert qa_response.json()["status"] == "passed"
        download = client.get(job["result"]["files"]["video_16_9.mp4"])
        assert download.status_code == 200
        assert download.content[4:8] == b"ftyp"
        manifest = json.loads(client.get(job["result"]["files"]["manifest.json"]).text)
        assert "warnings" in manifest


@pytest.mark.e2e
def test_audio_upload_preserves_voiceover_and_whisper_caption_timing(
    tmp_path: Path,
    monkeypatch,
):
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg/FFprobe are required for the audio end-to-end test.")

    data_dir = tmp_path / "data"
    output_dir = data_dir / "outputs"
    data_dir.mkdir()
    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(main, "DB_PATH", data_dir / "jobs.sqlite3")
    monkeypatch.setenv("FOOTAGE_PROVIDER", "offline")

    run_real_whisper = os.getenv("RUN_WHISPER_INTEGRATION", "").lower() == "true"
    input_audio = tmp_path / "voice.wav"
    if run_real_whisper:
        _tts_audio(
            "This is a local speech recognition test.",
            input_audio,
            ffmpeg,
        )
    else:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=1.2",
                str(input_audio),
            ],
            check=True,
        )
        monkeypatch.setattr(
            "app.pipeline.transcribe_audio",
            lambda path, duration, language: (
                [
                    {"start": 0.1, "end": 0.5, "text": "First phrase."},
                    {"start": 0.65, "end": 0.95, "text": "Second phrase."},
                ],
                "en",
            ),
        )
    monkeypatch.setattr(
        "app.pipeline._tts_audio",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("TTS must not replace supplied audio.")
        ),
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/v1/broll/jobs/audio",
            headers={"Idempotency-Key": f"audio-e2e-{uuid.uuid4()}"},
            files={"audio_file": ("voice.wav", input_audio.read_bytes(), "audio/wav")},
        )
        assert response.status_code == 202
        accepted = response.json()
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            job = client.get(accepted["status_url"]).json()
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.1)
        assert job["status"] == "succeeded", job.get("error")

        timeline = json.loads(client.get(job["result"]["files"]["timeline.json"]).text)
        captions = client.get(job["result"]["files"]["captions.srt"]).text
        manifest = json.loads(client.get(job["result"]["files"]["manifest.json"]).text)
        assert timeline["timing_source"].startswith("faster-whisper")
        assert timeline["transcription_segments"]
        if not run_real_whisper:
            assert timeline["transcription_segments"] == [
                {"start": 0.1, "end": 0.5, "text": "First phrase."},
                {"start": 0.65, "end": 0.95, "text": "Second phrase."},
            ]
        assert all(segment["text"] in captions for segment in timeline["transcription_segments"])
        assert timeline["scenes"][0]["start"] == 0
        assert timeline["scenes"][0]["transcript_start"] == timeline["transcription_segments"][0]["start"]
        input_duration = float(subprocess.check_output(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(input_audio),
            ],
            text=True,
        ).strip())
        assert timeline["scenes"][-1]["end"] == pytest.approx(input_duration, abs=0.05)
        if not run_real_whisper:
            assert "00:00:00,100 --> 00:00:00,500" in captions
            assert "00:00:00,650 --> 00:00:00,950" in captions
        assert manifest["settings"]["input_mode"] == "audio"
        assert manifest["providers"][0]["name"] == "faster-whisper"

        output_path = output_dir / accepted["job_id"] / "video_16_9.mp4"
        probe = json.loads(subprocess.check_output(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration:stream=codec_type,codec_name",
                "-of", "json",
                str(output_path),
            ],
            text=True,
        ))
        assert float(probe["format"]["duration"]) == pytest.approx(input_duration, abs=0.12)
        assert any(
            stream.get("codec_type") == "audio" and stream.get("codec_name") == "aac"
            for stream in probe["streams"]
        )
