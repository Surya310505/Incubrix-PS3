import os
import shutil
from pathlib import Path

import pytest

from app.audio import transcribe_audio, validate_audio
from app.pipeline import _tts_audio


@pytest.mark.integration
def test_real_faster_whisper_transcribes_local_speech(tmp_path: Path):
    if os.getenv("RUN_WHISPER_INTEGRATION", "").lower() != "true":
        pytest.skip("Set RUN_WHISPER_INTEGRATION=true to download/load the configured Whisper model.")
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg/FFprobe are required for the Whisper integration test.")

    audio_path = tmp_path / "speech.wav"
    _tts_audio("This is a local speech recognition test.", audio_path, ffmpeg)
    info = validate_audio(audio_path, ffprobe=ffprobe, ffmpeg=ffmpeg)
    segments, language = transcribe_audio(audio_path, info.duration_seconds, "en")

    assert segments
    assert language == "en"
    assert all(0 <= segment["start"] < segment["end"] <= info.duration_seconds for segment in segments)
    assert all(
        segments[index]["end"] <= segments[index + 1]["start"]
        for index in range(len(segments) - 1)
    )
