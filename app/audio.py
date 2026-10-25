from __future__ import annotations

import ipaddress
import json
import math
import os
import re
import socket
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx


class AudioInputError(ValueError):
    pass


@dataclass(frozen=True)
class AudioInfo:
    duration_seconds: float
    codec: str
    container: str
    mean_volume_db: float


_MODEL_LOCK = threading.RLock()
_MODEL = None
_MODEL_KEY: tuple[str, str, str, str | None] | None = None


def _run(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioInputError("Audio validation exceeded its configured time limit.") from exc
    if result.returncode:
        raise AudioInputError("Audio file is corrupt or cannot be decoded.")
    return result


def validate_audio(
    path: Path,
    ffprobe: str = "ffprobe",
    ffmpeg: str = "ffmpeg",
    max_duration_seconds: int | None = None,
) -> AudioInfo:
    if not path.is_file() or path.stat().st_size == 0:
        raise AudioInputError("Audio input is empty or missing.")
    extension = path.suffix.lower()
    if extension not in {".mp3", ".wav", ".m4a"}:
        raise AudioInputError("Audio must use an MP3, WAV, or M4A file extension.")

    timeout = float(os.getenv("MEDIA_COMMAND_TIMEOUT_SECONDS", "300"))
    probe_result = _run(
        [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=format_name,duration:stream=codec_type,codec_name",
            "-of", "json",
            str(path),
        ],
        timeout,
    )
    try:
        probe = json.loads(probe_result.stdout)
        streams = probe["streams"]
        format_info = probe["format"]
        duration = float(format_info["duration"])
        audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
        video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
        codec = str(audio_streams[0]["codec_name"])
        container = str(format_info["format_name"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AudioInputError("Audio file has no valid audio stream or duration.") from exc
    if video_streams or not audio_streams:
        raise AudioInputError("Input must contain audio only.")
    if not math.isfinite(duration) or duration <= 0:
        raise AudioInputError("Audio duration must be greater than zero.")
    if max_duration_seconds is not None and duration > max_duration_seconds:
        raise AudioInputError(
            f"Audio duration exceeds the {max_duration_seconds}-second limit."
        )

    compatible = {
        ".mp3": ("mp3", {"mp3"}),
        ".wav": ("wav", {"wav"}),
        ".m4a": (
            "m4a",
            {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
        ),
    }
    expected_container, allowed_containers = compatible[extension]
    container_names = set(container.split(","))
    if not container_names.intersection(allowed_containers):
        raise AudioInputError(
            f"Audio content does not match its {expected_container.upper()} extension."
        )
    if extension == ".mp3" and codec != "mp3":
        raise AudioInputError("Audio content is not an MP3 stream.")

    volume_result = _run(
        [
            ffmpeg,
            "-hide_banner", "-nostats", "-i", str(path),
            "-vn", "-af", "volumedetect", "-f", "null", "-",
        ],
        timeout,
    )
    match = re.search(r"mean_volume:\s*(-inf|[-+]?\d+(?:\.\d+)?)\s*dB", volume_result.stderr)
    if not match:
        raise AudioInputError("Could not measure input audio volume.")
    mean_volume = float("-inf") if match.group(1) == "-inf" else float(match.group(1))
    if not math.isfinite(mean_volume) or mean_volume <= -55:
        raise AudioInputError("Audio is silent or too quiet to transcribe.")
    return AudioInfo(duration, codec, container, mean_volume)


def _allowed_audio_hosts() -> set[str]:
    value = os.getenv("AUDIO_URL_ALLOWED_HOSTS", "")
    return {
        host.strip().lower().rstrip(".")
        for host in value.split(",")
        if host.strip()
    }


def _validate_remote_url(url: str, allowed_hosts: set[str]) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise AudioInputError("Audio URLs must use HTTPS and must not include credentials.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise AudioInputError("Audio URL has an invalid port.") from exc
    if port not in (None, 443):
        raise AudioInputError("Audio URLs must use the standard HTTPS port.")
    if host not in allowed_hosts:
        raise AudioInputError("Audio URL host is not in AUDIO_URL_ALLOWED_HOSTS.")
    try:
        addresses = {
            ipaddress.ip_address(result[4][0])
            for result in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError) as exc:
        raise AudioInputError("Audio URL host could not be resolved.") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise AudioInputError("Audio URL host must resolve only to public IP addresses.")


def download_audio_url(
    url: str,
    destination: Path,
    max_bytes: int,
    timeout_seconds: float,
    transport: httpx.BaseTransport | None = None,
) -> tuple[str, str]:
    allowed_hosts = _allowed_audio_hosts()
    if not allowed_hosts:
        raise AudioInputError(
            "Audio URL downloads are disabled; configure AUDIO_URL_ALLOWED_HOSTS first."
        )
    current_url = url
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            for redirect_count in range(4):
                _validate_remote_url(current_url, allowed_hosts)
                with client.stream("GET", current_url, headers={"Accept": "audio/*"}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location or redirect_count == 3:
                            raise AudioInputError("Audio URL exceeded the redirect limit.")
                        current_url = urljoin(current_url, location)
                        continue
                    if response.status_code != 200:
                        raise AudioInputError(
                            f"Audio URL returned HTTP {response.status_code}."
                        )
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if not content_type.startswith("audio/") and content_type not in {
                        "application/octet-stream",
                        "video/mp4",
                    }:
                        raise AudioInputError("Audio URL did not return an audio media type.")
                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > max_bytes:
                        raise AudioInputError("Audio URL exceeds the configured download limit.")
                    total = 0
                    with destination.open("wb") as output:
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > max_bytes:
                                raise AudioInputError(
                                    "Audio URL exceeds the configured download limit."
                                )
                            output.write(chunk)
                    if total == 0:
                        raise AudioInputError("Audio URL returned an empty file.")
                    extension = Path(urlparse(current_url).path).suffix.lower()
                    if extension not in {".mp3", ".wav", ".m4a"}:
                        extension = {
                            "audio/mpeg": ".mp3",
                            "audio/mp3": ".mp3",
                            "audio/wav": ".wav",
                            "audio/x-wav": ".wav",
                            "audio/wave": ".wav",
                            "audio/mp4": ".m4a",
                            "audio/x-m4a": ".m4a",
                            "video/mp4": ".m4a",
                        }.get(content_type, "")
                    if not extension:
                        raise AudioInputError(
                            "Audio URL must identify an MP3, WAV, or M4A file."
                        )
                    return current_url, extension
    except AudioInputError:
        destination.unlink(missing_ok=True)
        raise
    except (httpx.HTTPError, OSError, ValueError) as exc:
        destination.unlink(missing_ok=True)
        raise AudioInputError(
            f"Audio URL download failed ({type(exc).__name__})."
        ) from exc
    destination.unlink(missing_ok=True)
    raise AudioInputError("Audio URL could not be downloaded.")


def _get_whisper_model():
    global _MODEL, _MODEL_KEY
    model_size = os.getenv("WHISPER_MODEL_SIZE", "base")
    device = os.getenv("WHISPER_DEVICE", "cpu")
    compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
    model_dir = os.getenv("WHISPER_MODEL_DIR") or None
    key = (model_size, device, compute_type, model_dir)
    with _MODEL_LOCK:
        if _MODEL is not None and _MODEL_KEY == key:
            return _MODEL
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is required for audio input; install the project requirements."
            ) from exc
        try:
            _MODEL = WhisperModel(
                model_size,
                device=device,
                compute_type=compute_type,
                download_root=model_dir,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Could not load faster-whisper model '{model_size}' ({type(exc).__name__})."
            ) from exc
        _MODEL_KEY = key
        return _MODEL


def transcribe_audio(
    path: Path,
    duration_seconds: float,
    language: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        with _MODEL_LOCK:
            model = _get_whisper_model()
            segments, info = model.transcribe(
                str(path),
                language=language,
                word_timestamps=False,
                vad_filter=True,
            )
            transcript_segments = list(segments)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Local audio transcription failed ({type(exc).__name__}).") from exc

    result: list[dict[str, Any]] = []
    previous_end = 0.0
    for segment in transcript_segments:
        text = str(getattr(segment, "text", "")).strip()
        start = float(getattr(segment, "start", float("nan")))
        end = float(getattr(segment, "end", float("nan")))
        if not text:
            continue
        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end <= start
            or start < previous_end
            or end > duration_seconds + 0.1
        ):
            raise RuntimeError("Whisper returned invalid or unordered segment timestamps.")
        end = min(end, duration_seconds)
        result.append({"start": start, "end": end, "text": text})
        previous_end = end
    if not result:
        raise RuntimeError("Whisper did not recognize speech in the audio.")
    language_code = getattr(info, "language", None)
    return result, language_code if isinstance(language_code, str) else language


def group_transcript_segments(
    segments: list[dict[str, float | str]],
    max_scenes: int,
) -> list[dict[str, float | str]]:
    if max_scenes < 1:
        raise ValueError("max_scenes must be positive.")
    if len(segments) <= max_scenes:
        return list(segments)
    group_size = math.ceil(len(segments) / max_scenes)
    grouped = []
    for offset in range(0, len(segments), group_size):
        group = segments[offset:offset + group_size]
        grouped.append({
            "start": group[0]["start"],
            "end": group[-1]["end"],
            "text": " ".join(str(segment["text"]).strip() for segment in group),
        })
    return grouped
