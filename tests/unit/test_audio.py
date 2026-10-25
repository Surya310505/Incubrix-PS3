import json
import socket
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from app import audio
from app.audio import AudioInputError


def test_transcription_returns_whisper_timestamps_without_retiming(monkeypatch, tmp_path):
    expected = [
        SimpleNamespace(start=0.42, end=1.17, text="  A first phrase. "),
        SimpleNamespace(start=1.38, end=2.06, text="The next phrase."),
    ]

    class Model:
        def transcribe(self, filename, **options):
            assert filename == str(tmp_path / "voice.wav")
            assert options == {
                "language": "en",
                "word_timestamps": False,
                "vad_filter": True,
            }
            return iter(expected), SimpleNamespace(language="en")

    monkeypatch.setattr(audio, "_get_whisper_model", lambda: Model())
    segments, language = audio.transcribe_audio(tmp_path / "voice.wav", 2.5, "en")
    assert segments == [
        {"start": 0.42, "end": 1.17, "text": "A first phrase."},
        {"start": 1.38, "end": 2.06, "text": "The next phrase."},
    ]
    assert language == "en"


def test_transcription_rejects_unordered_or_out_of_range_timestamps(monkeypatch, tmp_path):
    class Model:
        def transcribe(self, filename, **options):
            return iter([
                SimpleNamespace(start=0.8, end=1.2, text="First."),
                SimpleNamespace(start=0.5, end=0.9, text="Out of order."),
            ]), SimpleNamespace(language="en")

    monkeypatch.setattr(audio, "_get_whisper_model", lambda: Model())
    with pytest.raises(RuntimeError, match="invalid or unordered"):
        audio.transcribe_audio(tmp_path / "voice.wav", 2.0)


def test_grouping_respects_scene_limit_and_transcript_times():
    segments = [
        {"start": float(index), "end": index + 0.5, "text": f"Phrase {index}."}
        for index in range(5)
    ]
    grouped = audio.group_transcript_segments(segments, max_scenes=2)
    assert len(grouped) == 2
    assert grouped[0] == {
        "start": 0.0,
        "end": 2.5,
        "text": "Phrase 0. Phrase 1. Phrase 2.",
    }
    assert grouped[1] == {
        "start": 3.0,
        "end": 4.5,
        "text": "Phrase 3. Phrase 4.",
    }


def test_audio_validation_enforces_duration_container_and_silence(monkeypatch, tmp_path):
    audio_file = tmp_path / "voice.wav"
    audio_file.write_bytes(b"not-empty")
    probe = subprocess.CompletedProcess(
        args=["ffprobe"],
        returncode=0,
        stdout=json.dumps({
            "streams": [{"codec_type": "audio", "codec_name": "pcm_s16le"}],
            "format": {"duration": "3.0", "format_name": "wav"},
        }),
        stderr="",
    )
    volume = subprocess.CompletedProcess(
        args=["ffmpeg"],
        returncode=0,
        stdout="",
        stderr="mean_volume: -21.0 dB\n",
    )
    commands = iter([probe, volume])
    monkeypatch.setattr(audio, "_run", lambda command, timeout: next(commands))
    info = audio.validate_audio(audio_file, max_duration_seconds=4)
    assert info.duration_seconds == 3.0
    assert info.codec == "pcm_s16le"

    commands = iter([probe])
    monkeypatch.setattr(audio, "_run", lambda command, timeout: next(commands))
    with pytest.raises(AudioInputError, match="duration exceeds"):
        audio.validate_audio(audio_file, max_duration_seconds=2)

    silent = subprocess.CompletedProcess(
        args=["ffmpeg"],
        returncode=0,
        stdout="",
        stderr="mean_volume: -inf dB\n",
    )
    commands = iter([probe, silent])
    monkeypatch.setattr(audio, "_run", lambda command, timeout: next(commands))
    with pytest.raises(AudioInputError, match="silent"):
        audio.validate_audio(audio_file, max_duration_seconds=4)


def test_corrupt_audio_is_rejected(monkeypatch, tmp_path):
    audio_file = tmp_path / "voice.mp3"
    audio_file.write_bytes(b"corrupt")
    corrupt = subprocess.CompletedProcess(
        args=["ffprobe"],
        returncode=1,
        stdout="",
        stderr="invalid data",
    )
    monkeypatch.setattr(audio.subprocess, "run", lambda *args, **kwargs: corrupt)
    with pytest.raises(AudioInputError, match="corrupt"):
        audio.validate_audio(audio_file)


def test_audio_url_requires_explicit_host_allowlist(monkeypatch, tmp_path):
    monkeypatch.delenv("AUDIO_URL_ALLOWED_HOSTS", raising=False)
    with pytest.raises(AudioInputError, match="disabled"):
        audio.download_audio_url(
            "https://media.example.test/voice.wav",
            tmp_path / "voice.download",
            max_bytes=1024,
            timeout_seconds=2,
        )


def test_audio_url_download_respects_allowlist_and_byte_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIO_URL_ALLOWED_HOSTS", "media.example.test")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    requested = []

    def respond(request):
        requested.append(str(request.url))
        return httpx.Response(
            200,
            headers={"content-type": "audio/wav"},
            content=b"audio-data",
        )

    destination = tmp_path / "voice.download"
    with pytest.raises(AudioInputError, match="download limit"):
        audio.download_audio_url(
            "https://media.example.test/voice.wav",
            destination,
            max_bytes=5,
            timeout_seconds=2,
            transport=httpx.MockTransport(respond),
        )
    assert not destination.exists()
    assert requested == ["https://media.example.test/voice.wav"]

    final_url, extension = audio.download_audio_url(
        "https://media.example.test/voice.wav",
        destination,
        max_bytes=20,
        timeout_seconds=2,
        transport=httpx.MockTransport(respond),
    )
    assert final_url == "https://media.example.test/voice.wav"
    assert extension == ".wav"
    assert destination.read_bytes() == b"audio-data"


def test_audio_url_rejects_private_resolved_addresses(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIO_URL_ALLOWED_HOSTS", "media.example.test")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ],
    )
    with pytest.raises(AudioInputError, match="public IP"):
        audio.download_audio_url(
            "https://media.example.test/voice.wav",
            tmp_path / "voice.download",
            max_bytes=1024,
            timeout_seconds=2,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"would-be-private")
            ),
        )
