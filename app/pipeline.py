from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from importlib.metadata import version

from PIL import Image, ImageDraw, ImageFont, __version__ as PILLOW_VERSION

from app.audio import (
    AudioInputError,
    download_audio_url,
    group_transcript_segments,
    transcribe_audio,
    validate_audio,
)
from app.footage import (
    PEXELS_LICENSE_URL,
    FootageAsset,
    FootageProviderError,
    assess_asset_keywords,
    create_footage_provider,
)


def _run(command: list[str]) -> str:
    timeout = float(os.getenv("MEDIA_COMMAND_TIMEOUT_SECONDS", "300"))
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Command failed ({command[0]}): {result.stderr[-1000:]}")
    return result.stdout


def _probe_duration(path: Path, ffprobe: str) -> float:
    output = _run([
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    duration = float(output.strip())
    if duration <= 0:
        raise RuntimeError(f"Generated media has invalid duration: {path.name}")
    return duration


def _validate_video_source(path: Path, ffprobe: str, ffmpeg: str) -> None:
    output = _run([
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_type", "-of", "json", str(path),
    ])
    probe = json.loads(output)
    if not probe.get("streams"):
        raise RuntimeError("Downloaded footage has no readable video stream.")
    duration = _probe_duration(path, ffprobe)
    black_intervals, frozen_intervals = _inspect_video_frames(
        path,
        ffmpeg,
        duration,
        freeze_threshold_seconds=0.5,
    )
    if black_intervals or frozen_intervals:
        raise RuntimeError("Downloaded footage contains black or frozen frames.")


def _inspect_video_frames(
    path: Path,
    ffmpeg: str,
    duration_seconds: float,
    freeze_threshold_seconds: float = 1.0,
) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    result = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-i", str(path), "-an",
            "-vf",
            "blackdetect=d=0.1:pix_th=0.1,"
            f"freezedetect=n=-60dB:d={freeze_threshold_seconds}",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        timeout=float(os.getenv("MEDIA_COMMAND_TIMEOUT_SECONDS", "300")),
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Frame-quality validation failed for {path.name}.")

    black_intervals = [
        {
            "start": float(start),
            "end": float(end),
            "duration": float(frame_duration),
        }
        for start, end, frame_duration in re.findall(
            r"black_start:([-\d.]+)\s+black_end:([-\d.]+)\s+black_duration:([-\d.]+)",
            result.stderr,
        )
    ]
    freeze_events = re.findall(
        r"lavfi\.freezedetect\.(freeze_start|freeze_end):\s*([-\d.]+)",
        result.stderr,
    )
    frozen_intervals = []
    freeze_start = None
    for event, timestamp in freeze_events:
        timestamp_value = float(timestamp)
        if event == "freeze_start":
            freeze_start = timestamp_value
        elif freeze_start is not None:
            frozen_intervals.append({
                "start": freeze_start,
                "end": timestamp_value,
                "duration": timestamp_value - freeze_start,
            })
            freeze_start = None
    if freeze_start is not None:
        frozen_intervals.append({
            "start": freeze_start,
            "end": duration_seconds,
            "duration": max(0.0, duration_seconds - freeze_start),
        })
    return (
        [interval for interval in black_intervals if interval["duration"] >= 0.1],
        [
            interval for interval in frozen_intervals
            if interval["duration"] >= freeze_threshold_seconds
        ],
    )


def _split_sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()]


def _validate_scene_timing(
    scene_timing: list[tuple[float, float]],
    duration_seconds: float,
    tolerance_seconds: float = 0.01,
) -> None:
    if (
        not scene_timing
        or not math.isfinite(duration_seconds)
        or duration_seconds <= 0
    ):
        raise ValueError("Timeline must contain scenes and a positive narration duration.")
    if abs(scene_timing[0][0]) > tolerance_seconds:
        raise ValueError("Timeline must begin at the start of the narration.")
    previous_end = 0.0
    for start, end in scene_timing:
        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end <= start
            or end > duration_seconds + tolerance_seconds
        ):
            raise ValueError("Timeline contains an invalid scene interval.")
        if abs(start - previous_end) > tolerance_seconds:
            raise ValueError("Timeline contains a gap or overlap between scenes.")
        previous_end = end
    if abs(previous_end - duration_seconds) > tolerance_seconds:
        raise ValueError("Timeline does not cover the complete narration.")


def _srt_time(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def _fallback_visual_kind(text: str, context: str, scene_index: int) -> str:
    normalized_context = context.lower()
    if any(term in normalized_context for term in (
        "solar", "photovoltaic", "renewable energy", "clean energy",
    )):
        normalized_text = text.lower()
        categories = {
            "solar-panels": ("panel", "photovoltaic", "sunlight", "array", "roof"),
            "solar-home": ("house", "home", "family", "battery", "storage", "grid"),
            "solar-landscape": (
                "landscape", "wind", "clean", "renewable", "climate", "emission",
                "carbon", "future", "planet",
            ),
        }
        matches = [
            (sum(term in normalized_text for term in terms), kind)
            for kind, terms in categories.items()
        ]
        score, kind = max(matches)
        return kind if score else tuple(categories)[scene_index % len(categories)]
    if any(term in normalized_context for term in (
        "budget", "saving", "savings", "finance", "financial", "invest", "money",
    )):
        return "finance"
    if any(term in normalized_context for term in (
        "forest", "ocean", "wildlife", "nature", "climate", "environment",
    )):
        return "nature"
    if any(term in normalized_context for term in (
        "technology", "software", "digital", "computer", "data", "artificial intelligence",
    )):
        return "technology"
    if any(term in normalized_context for term in ("home", "house", "family")):
        return "home"
    return "story"


def _fallback_scene_image(
    text: str,
    context: str,
    scene_index: int,
    path: Path,
    width: int,
    height: int,
) -> str:
    kind = _fallback_visual_kind(text, context, scene_index)
    scale = min(1, 960 / max(width, height))
    image_width, image_height = round(width * scale), round(height * scale)
    image = Image.new("RGB", (image_width, image_height))
    draw = ImageDraw.Draw(image)

    top = (11, 30, 51) if kind != "nature" else (18, 48, 54)
    bottom = (77, 132, 143) if kind.startswith("solar") else (61, 111, 112)
    for y in range(image_height):
        fraction = y / max(1, image_height - 1)
        color = tuple(round(start + (end - start) * fraction) for start, end in zip(top, bottom))
        draw.line((0, y, image_width, y), fill=color)

    def p(x: float, y: float) -> tuple[int, int]:
        return round(image_width * x), round(image_height * y)

    def polygon(points: list[tuple[float, float]], fill: str) -> None:
        draw.polygon([p(x, y) for x, y in points], fill=fill)

    def ellipse(bounds: tuple[float, float, float, float], fill: str, outline: str | None = None) -> None:
        draw.ellipse((*p(bounds[0], bounds[1]), *p(bounds[2], bounds[3])),
                     fill=fill, outline=outline)

    font = ImageFont.load_default(size=max(14, round(image_height * 0.036)))
    small_font = ImageFont.load_default(size=max(10, round(image_height * 0.022)))
    label = {
        "solar-panels": "SOLAR FIELD / SUNLIGHT TO POWER",
        "solar-home": "SOLAR HOME / POWERING EVERYDAY LIFE",
        "solar-landscape": "CLEAN ENERGY / A BRIGHTER LANDSCAPE",
        "finance": "FINANCIAL WELLNESS / SMALL STEPS",
        "nature": "A HEALTHIER PLANET / IN BALANCE",
        "technology": "TECHNOLOGY / IDEAS IN MOTION",
        "home": "HOME / POWERING WHAT MATTERS",
        "story": "AN IDEA TAKING SHAPE",
    }[kind]
    draw.text(p(0.07, 0.075), label, font=small_font, fill=(218, 242, 236))
    draw.line((*p(0.07, 0.12), *p(0.22, 0.12)), fill=(245, 189, 85), width=max(2, image_height // 270))

    if kind.startswith("solar"):
        ellipse((0.68, 0.14, 0.88, 0.34), (255, 198, 91))
        ellipse((0.645, 0.105, 0.915, 0.375), (255, 203, 93))
        polygon([(0, 0.63), (0.22, 0.44), (0.42, 0.61), (0.66, 0.42),
                 (1, 0.62), (1, 1), (0, 1)], "#345a5b")
        polygon([(0, 0.73), (0.34, 0.56), (0.61, 0.72), (0.8, 0.55),
                 (1, 0.67), (1, 1), (0, 1)], "#203e43")
        if kind == "solar-panels":
            panel = [(0.08, 0.88), (0.93, 0.88), (0.71, 0.55), (0.3, 0.55)]
            polygon([(x, min(0.98, y + 0.035)) for x, y in panel], "#132b3d")
            polygon(panel, "#147a91")
            polygon([(0.3, 0.55), (0.71, 0.55), (0.67, 0.61), (0.34, 0.61)], "#49b4bd")
            for fraction in (0.2, 0.4, 0.6, 0.8):
                left = (panel[0][0] + (panel[3][0] - panel[0][0]) * fraction,
                        panel[0][1] + (panel[3][1] - panel[0][1]) * fraction)
                right = (panel[1][0] + (panel[2][0] - panel[1][0]) * fraction,
                         panel[1][1] + (panel[2][1] - panel[1][1]) * fraction)
                draw.line((*p(*left), *p(*right)), fill="#8adddf", width=max(1, image_height // 480))
            for fraction in (0.25, 0.5, 0.75):
                left = (panel[3][0] + (panel[0][0] - panel[3][0]) * fraction,
                        panel[3][1] + (panel[0][1] - panel[3][1]) * fraction)
                right = (panel[2][0] + (panel[1][0] - panel[2][0]) * fraction,
                         panel[2][1] + (panel[1][1] - panel[2][1]) * fraction)
                draw.line((*p(*left), *p(*right)), fill="#8adddf", width=max(1, image_height // 480))
            draw.line((*p(0.74, 0.34), *p(0.64, 0.49), *p(0.56, 0.49)),
                      fill="#ffd77e", width=max(2, image_height // 100))
            ellipse((0.54, 0.47, 0.58, 0.51), "#fff0b1")
        elif kind == "solar-home":
            polygon([(0.19, 0.57), (0.48, 0.37), (0.77, 0.57)], "#263d4a")
            polygon([(0.24, 0.56), (0.72, 0.56), (0.72, 0.82), (0.24, 0.82)], "#f0d5a6")
            polygon([(0.27, 0.55), (0.48, 0.405), (0.69, 0.55)], "#c96e4d")
            draw.rectangle((*p(0.35, 0.59), *p(0.47, 0.69)), fill="#ffd579")
            draw.rectangle((*p(0.54, 0.59), *p(0.65, 0.69)), fill="#ffd579")
            draw.rectangle((*p(0.45, 0.69), *p(0.53, 0.82)), fill="#745044")
            polygon([(0.35, 0.51), (0.5, 0.45), (0.61, 0.53), (0.46, 0.58)], "#146b82")
            draw.line((*p(0.72, 0.64), *p(0.82, 0.64)), fill="#ffd77e", width=max(2, image_height // 120))
            draw.rounded_rectangle((*p(0.79, 0.57), *p(0.91, 0.82)),
                                   radius=max(4, image_height // 80), fill="#e6edf0")
            draw.rectangle((*p(0.81, 0.60), *p(0.89, 0.65)), fill="#31596a")
            draw.text(p(0.825, 0.68), "+", font=font, fill="#388d6d")
            draw.line((*p(0.77, 0.57), *p(0.79, 0.57)), fill="#ffd77e", width=max(2, image_height // 120))
        else:
            for x, base_y, size in ((0.23, 0.78, 0.11), (0.72, 0.74, 0.14), (0.87, 0.82, 0.09)):
                draw.line((*p(x, base_y), *p(x, base_y - size)), fill="#edf0dc",
                          width=max(2, image_height // 180))
                ellipse((x - size * 0.13, base_y - size * 1.15,
                         x + size * 0.13, base_y - size * 0.89), "#edf0dc")
                for dx, dy in ((-0.09, -0.07), (0.09, -0.07), (0, 0.1)):
                    draw.line((*p(x, base_y - size), *p(x + dx, base_y - size + dy)),
                              fill="#edf0dc", width=max(2, image_height // 200))
            polygon([(0.08, 0.88), (0.5, 0.88), (0.43, 0.77), (0.15, 0.77)], "#147a91")
            for x in (0.22, 0.31, 0.4):
                draw.line((*p(x, 0.78), *p(x - 0.04, 0.88)), fill="#8adddf",
                          width=max(1, image_height // 480))
    elif kind == "finance":
        polygon([(0.08, 0.84), (0.92, 0.84), (0.92, 0.87), (0.08, 0.87)], "#d8e6e2")
        for index, height_fraction in enumerate((0.24, 0.38, 0.31, 0.55, 0.7)):
            x = 0.17 + index * 0.13
            polygon([(x, 0.84 - height_fraction), (x + 0.075, 0.84 - height_fraction),
                     (x + 0.075, 0.84), (x, 0.84)], "#45c2a1" if index < 4 else "#ffcb72")
        points = [p(x, y) for x, y in ((0.15, 0.64), (0.36, 0.53), (0.55, 0.57), (0.81, 0.3))]
        draw.line(points, fill="#ffe19a", width=max(3, image_height // 100))
        for x, y in ((0.15, 0.64), (0.36, 0.53), (0.55, 0.57), (0.81, 0.3)):
            ellipse((x - 0.018, y - 0.032, x + 0.018, y + 0.032), "#fff0b1")
    elif kind == "technology":
        draw.rounded_rectangle((*p(0.3, 0.33), *p(0.7, 0.75)),
                               radius=max(12, image_height // 35), fill="#1c4255",
                               outline="#73d7cf", width=max(3, image_height // 160))
        draw.rounded_rectangle((*p(0.39, 0.42), *p(0.61, 0.65)),
                               radius=max(8, image_height // 50), fill="#f2ae59")
        for index in range(5):
            x = 0.34 + index * 0.08
            draw.line((*p(x, 0.24), *p(x, 0.33)), fill="#73d7cf", width=max(2, image_height // 160))
            draw.line((*p(x, 0.75), *p(x, 0.84)), fill="#73d7cf", width=max(2, image_height // 160))
        for x in (0.17, 0.7, 0.84):
            draw.line((*p(x, 0.53), *p(0.3 if x < 0.3 else 0.7, 0.53)),
                      fill="#73d7cf", width=max(2, image_height // 160))
            ellipse((x - 0.025, 0.505, x + 0.025, 0.555), "#ffe19a")
    elif kind == "home":
        ellipse((0.69, 0.18, 0.85, 0.34), "#ffd77e")
        polygon([(0.2, 0.58), (0.5, 0.34), (0.8, 0.58)], "#c96e4d")
        polygon([(0.26, 0.56), (0.74, 0.56), (0.74, 0.83), (0.26, 0.83)], "#f0d5a6")
        draw.rectangle((*p(0.36, 0.61), *p(0.47, 0.71)), fill="#ffd579")
        draw.rectangle((*p(0.54, 0.61), *p(0.65, 0.71)), fill="#ffd579")
        draw.rectangle((*p(0.46, 0.71), *p(0.54, 0.83)), fill="#745044")
        polygon([(0, 0.82), (0.3, 0.7), (0.55, 0.83), (0.81, 0.69),
                 (1, 0.8), (1, 1), (0, 1)], "#31564d")
    else:
        ellipse((0.68, 0.18, 0.86, 0.36), "#ffd77e")
        polygon([(0, 0.7), (0.24, 0.49), (0.43, 0.68), (0.67, 0.45),
                 (1, 0.7), (1, 1), (0, 1)], "#426b66")
        polygon([(0, 0.83), (0.31, 0.66), (0.62, 0.82), (0.84, 0.64),
                 (1, 0.78), (1, 1), (0, 1)], "#274b4a")
        card = text.strip().replace("\n", " ")
        if len(card) > 52:
            card = card[:49].rsplit(" ", 1)[0] + "..."
        card_font = ImageFont.load_default(size=max(18, round(image_height * 0.045)))
        bounds = draw.textbbox((0, 0), card, font=card_font)
        card_width = min(image_width * 0.84, bounds[2] - bounds[0] + round(image_width * 0.06))
        card_height = bounds[3] - bounds[1] + round(image_height * 0.06)
        left = (image_width - card_width) / 2
        top = image_height * 0.42
        draw.rounded_rectangle((left, top, left + card_width, top + card_height),
                               radius=max(8, image_height // 60), fill=(16, 44, 61),
                               outline="#8ad9d0", width=max(1, image_height // 300))
        draw.text((left + round(image_width * 0.03), top + round(image_height * 0.03)),
                  card, font=card_font, fill=(255, 255, 255))

    path.parent.mkdir(parents=True, exist_ok=True)
    image.resize((width, height), Image.Resampling.LANCZOS).save(path)
    return kind


def _tts_audio(text: str, destination: Path, ffmpeg: str) -> dict[str, str]:
    espeak = os.getenv("ESPEAK_BINARY", "espeak-ng")
    say = os.getenv("SAY_BINARY", "say")
    espeak_path, say_path = shutil.which(espeak), shutil.which(say)
    raw = destination.with_suffix(".raw.wav" if espeak_path else ".aiff")
    if espeak_path:
        engine_voice = os.getenv("ESPEAK_VOICE", "en")
        _run([espeak_path, "-v", engine_voice, "-w", str(raw), text])
        provider = "espeak-ng"
        source = raw
    elif say_path:
        engine_voice = os.getenv("SAY_VOICE", "")
        command = [say_path]
        if engine_voice:
            command.extend(["-v", engine_voice])
        else:
            engine_voice = "platform-default"
        command.extend(["-o", str(raw), text])
        _run(command)
        provider = "macOS-say"
        source = raw
    else:
        raise RuntimeError("No local TTS engine found; install espeak-ng or configure a TTS adapter.")
    _run([
        ffmpeg, "-y", "-v", "error", "-i", str(source), "-ac", "1",
        "-ar", "48000", "-c:a", "pcm_s16le", str(destination),
    ])
    raw.unlink(missing_ok=True)
    return {"name": provider, "version": "system-managed; exact version not queried", "voice": engine_voice}


def _caption_image(text: str, path: Path, width: int, height: int) -> None:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=max(32, height // 24))
    max_width = int(width * 0.82)
    lines: list[str] = []
    line = ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if line and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    line_height = max(1, draw.textbbox((0, 0), "Ag", font=font)[3] + 8)
    padding = max(16, height // 72)
    box_height = len(lines) * line_height + 2 * padding
    box_top = height - int(height * 0.08) - box_height
    box_left, box_right = int(width * 0.06), int(width * 0.94)
    draw.rounded_rectangle(
        (box_left, box_top, box_right, box_top + box_height),
        radius=padding,
        fill=(0, 0, 0, 190),
    )
    for index, caption_line in enumerate(lines):
        bounds = draw.textbbox((0, 0), caption_line, font=font)
        x = (width - (bounds[2] - bounds[0])) // 2
        draw.text((x, box_top + padding + index * line_height), caption_line,
                  font=font, fill=(255, 255, 255, 255))
    image.save(path)


def render_script_job(
    job_id: str,
    payload: dict[str, Any],
    output_dir: Path,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
) -> dict[str, Any]:
    started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    work = output_dir / "work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(exist_ok=True)
    ffmpeg = os.getenv("FFMPEG_BINARY", ffmpeg)
    ffprobe = os.getenv("FFPROBE_BINARY", ffprobe)
    is_audio_input = bool(payload.get("audio_path") or payload.get("audio_url"))
    audio_source: Path | None = None
    audio_info = None
    transcription_segments: list[dict[str, Any]] = []
    recognized_language = payload.get("language")
    providers: dict[str, dict[str, Any]] = {}
    if is_audio_input:
        audio_path_value = payload.get("audio_path")
        if audio_path_value:
            audio_source = Path(audio_path_value)
            if not audio_source.is_file():
                raise AudioInputError("Uploaded audio file is missing from job storage.")
        else:
            max_bytes = int(os.getenv("MAX_AUDIO_UPLOAD_BYTES", str(100 * 1024 * 1024)))
            timeout_seconds = float(os.getenv("AUDIO_DOWNLOAD_TIMEOUT_SECONDS", "30"))
            downloaded_path = work / "remote-audio.download"
            _, extension = download_audio_url(
                str(payload["audio_url"]),
                downloaded_path,
                max_bytes,
                timeout_seconds,
            )
            audio_source = work / f"remote-audio{extension}"
            downloaded_path.replace(audio_source)
        audio_info = validate_audio(
            audio_source,
            ffprobe=ffprobe,
            ffmpeg=ffmpeg,
            max_duration_seconds=int(os.getenv("MAX_AUDIO_SECONDS", "600")),
        )
        transcription_segments, recognized_language = transcribe_audio(
            audio_source,
            audio_info.duration_seconds,
            payload.get("language"),
        )
        max_scenes = int(os.getenv("MAX_SCENES", "100"))
        scenes = group_transcript_segments(transcription_segments, max_scenes)
        providers["faster-whisper"] = {
            "name": "faster-whisper",
            "version": version("faster-whisper"),
            "model_size": os.getenv("WHISPER_MODEL_SIZE", "base"),
            "device": os.getenv("WHISPER_DEVICE", "cpu"),
            "compute_type": os.getenv("WHISPER_COMPUTE_TYPE", "int8"),
            "language": recognized_language,
        }
        cursor = 0.0
        scene_timing = []
        for index, scene in enumerate(scenes):
            next_start = (
                float(scenes[index + 1]["start"])
                if index + 1 < len(scenes)
                else audio_info.duration_seconds
            )
            scene_end = min(audio_info.duration_seconds, max(float(scene["end"]), next_start))
            scene_timing.append((cursor, scene_end))
            cursor = scene_end
        if scene_timing:
            scene_timing[-1] = (scene_timing[-1][0], audio_info.duration_seconds)
        caption_rows = [
            (
                float(segment["start"]),
                float(segment["end"]),
                str(segment["text"]),
            )
            for segment in transcription_segments
        ]
        narration = audio_source
        timing_source = "faster-whisper segment timestamps and FFprobe audio duration"
    else:
        scenes = payload.get("scenes") or [
            {"text": sentence, "visual_hint": None}
            for sentence in _split_sentences(payload.get("script", ""))
        ]
        scenes = [scene for scene in scenes if str(scene.get("text", "")).strip()]
        if not scenes:
            raise ValueError("No usable narration sentences were found.")
        scene_timing = []
        caption_rows = []
        narration = work / "narration.wav"
        cursor = 0.0
        timing_source = "measured duration of separately synthesized narration segments"
        audio_list = work / "audio.txt"
        with audio_list.open("w", encoding="utf-8") as listing:
            for index, scene in enumerate(scenes):
                text = str(scene["text"]).strip()
                audio_path = work / f"speech-{index:04}.wav"
                provider = _tts_audio(text, audio_path, ffmpeg)
                providers[provider["name"]] = provider
                segment_duration = _probe_duration(audio_path, ffprobe)
                scene_timing.append((cursor, cursor + segment_duration))
                caption_rows.append((cursor, cursor + segment_duration, text))
                cursor += segment_duration
                listing.write(f"file '{audio_path.resolve().as_posix()}'\n")
        _run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i",
              str(audio_list), "-c:a", "pcm_s16le", "-ar", "48000", str(narration)])
    if not scenes:
        raise ValueError("No speech scenes were found.")

    ratios = {
        "16:9": (1920, 1080),
        "9:16": (1080, 1920),
        "1:1": (1080, 1080),
    }
    requested_ratios = payload.get("aspect_ratios") or ["16:9"]
    target_dimensions = ratios[requested_ratios[0]]
    orientation = {
        "16:9": "landscape",
        "9:16": "portrait",
        "1:1": "square",
    }[requested_ratios[0]]
    scene_assets: list[FootageAsset | None] = [None] * len(scenes)
    scene_media_paths: list[Path | None] = [None] * len(scenes)
    scene_fallback_reasons: list[str | None] = [None] * len(scenes)
    scene_keyword_overlaps: list[float | None] = [None] * len(scenes)
    footage_warnings: list[str] = []
    footage_provider = create_footage_provider()
    footage_provider_used = footage_provider is not None
    used_asset_ids: set[str] = set()
    if footage_provider is not None:
        try:
            for index, scene in enumerate(scenes):
                query = str(scene.get("visual_hint") or scene.get("text") or "").strip()
                avoid_terms = [str(term) for term in payload.get("avoid", [])]
                try:
                    candidates = footage_provider.search(query, orientation, target_dimensions)
                except FootageProviderError as exc:
                    scene_fallback_reasons[index] = str(exc)
                    footage_warnings.append(
                        f"Scene {index + 1}: {exc} Procedural visual fallback used."
                    )
                    continue
                if not candidates:
                    scene_fallback_reasons[index] = "Pexels returned no usable footage."
                    footage_warnings.append(
                        f"Scene {index + 1}: no usable Pexels result; procedural visual fallback used."
                    )
                    continue
                candidate_failure_reason = None
                ranked_candidates: list[tuple[float, FootageAsset]] = []
                for candidate in candidates:
                    keyword_overlap, rejection_reason = assess_asset_keywords(
                        candidate,
                        query,
                        avoid_terms,
                    )
                    if rejection_reason:
                        candidate_failure_reason = rejection_reason
                        footage_warnings.append(
                            f"Scene {index + 1}: rejected Pexels candidate "
                            f"{candidate.asset_id}: {rejection_reason}"
                        )
                        continue
                    if keyword_overlap is not None:
                        ranked_candidates.append((keyword_overlap, candidate))
                ranked_candidates.sort(
                    key=lambda item: (-item[0], item[1].result_rank)
                )
                for keyword_overlap, candidate in ranked_candidates:
                    if candidate.asset_id in used_asset_ids:
                        candidate_failure_reason = (
                            "Candidate footage was already used by another scene."
                        )
                        continue
                    media_path = work / f"source-{index:04}.mp4"
                    try:
                        footage_provider.download(candidate, media_path)
                    except FootageProviderError as exc:
                        candidate_failure_reason = str(exc)
                        footage_warnings.append(
                            f"Scene {index + 1}: {exc} Tried the next Pexels result."
                        )
                        continue
                    try:
                        _validate_video_source(media_path, ffprobe, ffmpeg)
                    except (RuntimeError, ValueError) as exc:
                        media_path.unlink(missing_ok=True)
                        candidate_failure_reason = (
                            f"Downloaded Pexels footage failed media validation ({type(exc).__name__})."
                        )
                        footage_warnings.append(
                            f"Scene {index + 1}: downloaded Pexels footage failed media validation "
                            f"({type(exc).__name__}); tried the next result."
                        )
                        continue
                    scene_assets[index] = candidate
                    scene_media_paths[index] = media_path
                    scene_keyword_overlaps[index] = keyword_overlap
                    used_asset_ids.add(candidate.asset_id)
                    break
                if scene_assets[index] is None:
                    scene_fallback_reasons[index] = candidate_failure_reason or (
                        "No downloadable, non-repeated Pexels result was available."
                    )
                    footage_warnings.append(
                        f"Scene {index + 1}: no downloadable, non-repeated Pexels result; "
                        "procedural visual fallback used."
                    )
        finally:
            footage_provider.close()

    if not caption_rows:
        raise ValueError("No non-empty speech segments were provided.")
    duration = (
        audio_info.duration_seconds
        if audio_info
        else _probe_duration(narration, ffprobe)
    )
    _validate_scene_timing(scene_timing, duration)
    srt = output_dir / "captions.srt"
    with srt.open("w", encoding="utf-8") as handle:
        for index, (start, end, text) in enumerate(caption_rows, 1):
            handle.write(f"{index}\n{_srt_time(start)} --> {_srt_time(min(end, duration))}\n{text}\n\n")

    timeline_scenes = []
    visual_context = " ".join(
        f"{scene.get('text', '')} {scene.get('visual_hint') or ''}"
        for scene in scenes
    )
    for index, ((start, end), scene) in enumerate(zip(scene_timing, scenes)):
        text = str(scene["text"]).strip()
        asset = scene_assets[index]
        if asset:
            source = {
                "type": "stock-footage",
                "provider": "Pexels",
                "asset_id": asset.asset_id,
                "url": asset.source_url,
                "download_url": asset.file_url,
                "licence": "Pexels License; subject to its terms and any third-party rights",
                "licence_reference": PEXELS_LICENSE_URL,
                "creator": {"name": asset.creator_name, "url": asset.creator_url},
                "attribution": asset.attribution,
                "licence_evidence": "Provider source and creator URLs recorded; individual third-party rights not independently verified.",
            }
            clip_id = asset.asset_id
            is_procedurally_generated = False
            selection_reason = (
                f"Pexels result rank {asset.result_rank}; title-keyword overlap "
                f"{scene_keyword_overlaps[index]:.2f}; first downloadable, non-repeated "
                "candidate meeting the local keyword gate."
            )
        else:
            source = {
                "type": "procedural-offline-visual",
                "asset_id": f"generated-scene-{index + 1}",
                "url": None,
                "licence": "not applicable; generated graphic, not stock footage",
                "licence_reference": None,
                "creator": None,
                "attribution": None,
                "licence_evidence": (
                    "Original graphic rendered locally; this is not evidence of "
                    "commercial B-roll rights."
                ),
            }
            clip_id = f"generated-scene-{index + 1}"
            is_procedurally_generated = True
            visual_kind = _fallback_visual_kind(text, visual_context, index)
            selection_reason = (
                f"Original locally generated {visual_kind.replace('-', ' ')} illustration; "
                "not stock footage."
            )
        timeline_scene = {
            "scene_id": index + 1,
            "narration_text": text,
            "start": round(start, 3),
            "end": round(min(end, duration), 3),
            "clip": clip_id,
            "source": source,
            "is_ai_generated": False,
            "is_procedurally_generated": is_procedurally_generated,
            "selection_reason": selection_reason,
            "relevance_score": None,
            "title_keyword_overlap": scene_keyword_overlaps[index],
            "visual_hint": scene.get("visual_hint"),
        }
        if is_audio_input:
            timeline_scene["transcript_start"] = round(float(scene["start"]), 3)
            timeline_scene["transcript_end"] = round(float(scene["end"]), 3)
        if scene_fallback_reasons[index]:
            timeline_scene["fallback_reason"] = scene_fallback_reasons[index]
        timeline_scenes.append(timeline_scene)
    timeline = {
        "job_id": job_id,
        "timing_source": timing_source,
        "scenes": timeline_scenes,
    }
    if is_audio_input:
        timeline["transcription_segments"] = [
            {
                "start": round(float(segment["start"]), 3),
                "end": round(float(segment["end"]), 3),
                "text": str(segment["text"]),
            }
            for segment in transcription_segments
        ]
    (output_dir / "timeline.json").write_text(json.dumps(timeline, indent=2, ensure_ascii=False))

    video_lists: dict[str, Path] = {}
    burn_in = payload.get("captions", {}).get("burn_in", True)
    for ratio in requested_ratios:
        width, height = ratios[ratio]
        segment_list = work / f"video-{ratio.replace(':', '-')}.txt"
        with segment_list.open("w", encoding="utf-8") as listing:
            for index, (start, end) in enumerate(scene_timing):
                segment_duration = max(0.1, end - start)
                segment = work / f"visual-{ratio.replace(':', '-')}-{index:04}.mp4"
                command = [
                    ffmpeg, "-y", "-v", "error",
                ]
                source_path = scene_media_paths[index]
                if source_path:
                    command.extend(["-stream_loop", "-1", "-i", str(source_path)])
                    base_filter = (
                        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                        f"crop={width}:{height},fps=30,format=yuv420p"
                    )
                else:
                    scene_image = work / (
                        f"fallback-{ratio.replace(':', '-')}-{index:04}.png"
                    )
                    _fallback_scene_image(
                        str(scenes[index].get("text", "")),
                        visual_context,
                        index,
                        scene_image,
                        width,
                        height,
                    )
                    command.extend([
                        "-loop", "1", "-framerate", "30", "-i", str(scene_image),
                    ])
                    base_filter = (
                        "zoompan=z='1.05+0.01*sin(on/90)':"
                        "x='iw/2-(iw/zoom/2)+8*sin(on/37)':"
                        "y='ih/2-(ih/zoom/2)+8*cos(on/53)':d=1:"
                        f"s={width}x{height}:fps=30,format=yuv420p"
                    )
                overlapping_captions = [
                    (caption_index, caption_start, caption_end, caption_text)
                    for caption_index, (caption_start, caption_end, caption_text)
                    in enumerate(caption_rows)
                    if caption_start < end and caption_end > start
                ] if burn_in else []
                if overlapping_captions:
                    filter_parts = [f"[0:v]{base_filter}[base0]"]
                    previous_label = "[base0]"
                    for caption_input_index, (cue_index, cue_start, cue_end, cue_text) in enumerate(
                        overlapping_captions,
                        start=1,
                    ):
                        caption = work / (
                            f"caption-{ratio.replace(':', '-')}-{index:04}-{cue_index:04}.png"
                        )
                        _caption_image(cue_text, caption, width, height)
                        command.extend(["-loop", "1", "-i", str(caption)])
                        image_label = f"[caption{caption_input_index}]"
                        output_label = f"[base{caption_input_index}]"
                        relative_start = max(0.0, cue_start - start)
                        relative_end = min(segment_duration, cue_end - start)
                        filter_parts.extend([
                            f"[{caption_input_index}:v]format=rgba{image_label}",
                            f"{previous_label}{image_label}overlay=0:0:"
                            f"enable='between(t,{relative_start:.3f},{relative_end:.3f})'"
                            f"{output_label}",
                        ])
                        previous_label = output_label
                    filter_parts.append(f"{previous_label}format=yuv420p[out]")
                    command.extend([
                        "-filter_complex",
                        ";".join(filter_parts),
                        "-map",
                        "[out]",
                    ])
                else:
                    command.extend(["-vf", base_filter])
                command.extend([
                    "-an", "-t", f"{segment_duration:.6f}", "-c:v", "libx264",
                    "-preset", "ultrafast", "-r", "30", str(segment),
                ])
                _run(command)
                listing.write(f"file '{segment.resolve().as_posix()}'\n")
        video_lists[ratio] = segment_list

    files: dict[str, str] = {}
    video_qa: dict[str, Any] = {
        "checks": {
            "black_frames": {"threshold_seconds": 0.1, "result": "pending"},
            "frozen_frames": {"threshold_seconds": 1.0, "result": "pending"},
            "audio_video_sync_tolerance_seconds": 0.15,
        },
        "outputs": {},
    }
    for ratio, segment_list in video_lists.items():
        width, height = ratios[ratio]
        output_name = f"video_{ratio.replace(':', '_')}.mp4"
        output_path = output_dir / output_name
        filters = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps=30,format=yuv420p"
        _run([
            ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0",
            "-i", str(segment_list), "-i", str(narration), "-vf", filters,
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset",
            "ultrafast", "-profile:v", "high", "-level", "4.2", "-r", "30",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-ar", "48000", "-shortest", "-movflags", "+faststart", str(output_path),
        ])
        probe = json.loads(_run([
            ffprobe, "-v", "error", "-show_entries",
            "stream=codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,"
            "pix_fmt,time_base,sample_rate,start_time,duration",
            "-show_entries", "format=format_name,duration", "-of", "json", str(output_path),
        ]))
        streams = probe.get("streams", [])
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
        if (video.get("codec_name") != "h264" or audio.get("codec_name") != "aac"
                or video.get("width") != width or video.get("height") != height
                or video.get("r_frame_rate") != "30/1"
                or video.get("avg_frame_rate") != "30/1"
                or video.get("pix_fmt") != "yuv420p"
                or audio.get("sample_rate") != "48000"
                or "mp4" not in probe.get("format", {}).get("format_name", "").split(",")):
            raise RuntimeError(f"Output validation failed for {output_name}.")
        try:
            track_durations = (float(video["duration"]), float(audio["duration"]))
            container_duration = float(probe["format"]["duration"])
            video_start = float(video.get("start_time", 0))
            audio_start = float(audio.get("start_time", 0))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Output duration validation failed for {output_name}.") from exc
        if any(abs(track_duration - duration) > 0.15 for track_duration in track_durations):
            raise RuntimeError(f"Output does not cover the full narration in {output_name}.")
        if abs(container_duration - duration) > 0.15:
            raise RuntimeError(f"Output duration validation failed for {output_name}.")
        start_drift = abs(video_start - audio_start)
        duration_drift = abs(track_durations[0] - track_durations[1])
        if max(start_drift, duration_drift) > 0.15:
            raise RuntimeError(f"Audio/video synchronization validation failed for {output_name}.")

        black_intervals, frozen_intervals = _inspect_video_frames(
            output_path,
            ffmpeg,
            container_duration,
        )
        video_qa["outputs"][output_name] = {
            "container": probe["format"]["format_name"],
            "video": {
                "codec": video["codec_name"],
                "width": video["width"],
                "height": video["height"],
                "frame_rate": video["avg_frame_rate"],
                "pixel_format": video["pix_fmt"],
                "time_base": video["time_base"],
                "start_time_seconds": video_start,
                "duration_seconds": track_durations[0],
            },
            "audio": {
                "codec": audio["codec_name"],
                "sample_rate": audio["sample_rate"],
                "start_time_seconds": audio_start,
                "duration_seconds": track_durations[1],
            },
            "audio_video_start_drift_seconds": start_drift,
            "audio_video_duration_drift_seconds": duration_drift,
            "black_intervals": black_intervals,
            "frozen_intervals": frozen_intervals,
        }
        if black_intervals:
            raise RuntimeError(f"Black-frame validation failed for {output_name}.")
        if frozen_intervals:
            raise RuntimeError(f"Frozen-frame validation failed for {output_name}.")
        files[output_name] = f"/api/v1/jobs/{job_id}/files/{output_name}"

    video_qa["checks"]["black_frames"]["result"] = "passed"
    video_qa["checks"]["frozen_frames"]["result"] = "passed"
    video_qa["status"] = "passed"
    (output_dir / "video-qa.json").write_text(
        json.dumps(video_qa, indent=2, ensure_ascii=False)
    )
    files["video-qa.json"] = f"/api/v1/jobs/{job_id}/files/video-qa.json"
    if not payload.get("captions", {}).get("srt", True):
        srt.unlink(missing_ok=True)
    files["timeline.json"] = f"/api/v1/jobs/{job_id}/files/timeline.json"
    if payload.get("captions", {}).get("srt", True):
        files["captions.srt"] = f"/api/v1/jobs/{job_id}/files/captions.srt"
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    pexels_assets = [asset for asset in scene_assets if asset]
    if footage_provider_used:
        providers["Pexels API"] = {
            "name": "Pexels API",
            "version": "v1",
            "license_reference": PEXELS_LICENSE_URL,
        }
    fallback_used = len(pexels_assets) != len(scenes)
    manifest_warnings = []
    if footage_provider_used:
        manifest_warnings.extend([
            "Footage matching uses title-keyword overlap only; it is not semantic relevance scoring.",
            "Avoid terms are checked only against candidate page URL text; footage contents are not visually screened.",
        ])
    if is_audio_input:
        manifest_warnings.append(
            "Transcription is generated locally by faster-whisper; transcript accuracy requires review."
        )
    else:
        manifest_warnings.append(
            "The request voice field is metadata; select the local speech voice with environment configuration."
        )
    if fallback_used:
        manifest_warnings.append(
            "Original locally generated illustrations were used where footage was unavailable; "
            "they are not stock B-roll and commercial suitability has not been assessed."
        )
    if pexels_assets:
        manifest_warnings.extend([
            "Pexels API use for this product requires confirmation of product-specific permission.",
            "Pexels content may carry additional third-party rights; those rights were not independently verified.",
        ])
    manifest_warnings.extend(footage_warnings)
    attribution_notice = (
        {
            "text": "Footage provided by Pexels",
            "url": "https://www.pexels.com/",
        }
        if pexels_assets else None
    )
    manifest = {
        "job_id": job_id,
        "models": [],
        "providers": [
            *[providers[name] for name in sorted(providers)],
            {"name": "FFmpeg", "version": _run([ffmpeg, "-version"]).splitlines()[0]},
            {"name": "FFprobe", "version": _run([ffprobe, "-version"]).splitlines()[0]},
            {"name": "Pillow", "version": PILLOW_VERSION},
        ],
        "settings": {
            "voice": payload.get("voice"),
            "input_mode": "audio" if is_audio_input else "script",
            "language": recognized_language if is_audio_input else None,
            "input_audio_duration_seconds": duration if is_audio_input else None,
            "aspect_ratios": requested_ratios,
            "style": payload.get("style"),
            "avoid": payload.get("avoid", []),
            "captions": payload.get("captions"),
        },
        "seed": None,
        "timings_ms": {"total": elapsed_ms},
        "video_quality": {
            "status": video_qa["status"],
            "report_file": "video-qa.json",
            "outputs": video_qa["outputs"],
        },
        "cost_estimate": {
            "amount": None,
            "currency": "USD",
            "basis": (
                "local compute cost not measured; Pexels API usage is subject to its terms"
                if pexels_assets else "local compute cost not measured; no external services invoked"
            ),
        },
        "warnings": manifest_warnings,
        "attribution_notice": attribution_notice,
        "licence_rights": (
            {
                "status": "Pexels License referenced; product API permission and individual third-party rights not independently verified",
                "provider": "Pexels",
                "reference": PEXELS_LICENSE_URL,
                "asset_count": len(pexels_assets),
            }
            if pexels_assets
            else {
                "status": (
                    "no third-party footage used; generated illustrations are not stock B-roll "
                    "and commercial suitability has not been assessed"
                )
            }
        ),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    files["manifest.json"] = f"/api/v1/jobs/{job_id}/files/manifest.json"
    shutil.rmtree(work)
    result = {"status": "succeeded", "files": files, "duration_seconds": round(duration, 3)}
    if attribution_notice:
        result["attribution_notice"] = attribution_notice
    return result
