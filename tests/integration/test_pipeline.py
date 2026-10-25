from io import BytesIO
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat

from app.footage import FootageAsset
from app.audio import AudioInputError, validate_audio
from app.pipeline import (
    _fallback_scene_image,
    _inspect_video_frames,
    _validate_video_source,
    render_script_job,
)


@pytest.mark.unit
def test_sentence_splitter_preserves_sentence_punctuation():
    from app.pipeline import _split_sentences

    assert _split_sentences("First sentence. Is this second? Yes!") == [
        "First sentence.", "Is this second?", "Yes!",
    ]


@pytest.mark.unit
def test_solar_fallback_creates_distinct_topic_relevant_original_graphics(tmp_path: Path):
    context = (
        "Solar panels capture sunlight and convert it into electricity. "
        "A solar-powered home stores clean energy in a battery. "
        "Wind turbines and solar farms reduce carbon emissions."
    )
    scenes = (
        "Solar panels capture sunlight and convert it into electricity.",
        "A solar-powered home stores clean energy in a battery.",
        "Wind turbines and solar farms reduce carbon emissions.",
    )
    paths = [tmp_path / f"scene-{index}.png" for index in range(len(scenes))]

    kinds = [
        _fallback_scene_image(scene, context, index, paths[index], 640, 360)
        for index, scene in enumerate(scenes)
    ]

    assert kinds == ["solar-panels", "solar-home", "solar-landscape"]
    images = [Image.open(path).convert("RGB") for path in paths]
    assert all(image.size == (640, 360) for image in images)
    assert all(
        ImageChops.difference(images[0], image).getbbox()
        for image in images[1:]
    )


@pytest.mark.integration
def test_frame_quality_check_detects_black_and_frozen_video(tmp_path: Path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is required for frame-quality checks.")

    for color, expected in (("black", "black"), ("blue", "frozen")):
        video = tmp_path / f"{color}.mp4"
        subprocess.run(
            [
                ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
                f"color=c={color}:s=320x180:r=30:d=1.5", "-an",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
            ],
            check=True,
        )
        black_intervals, frozen_intervals = _inspect_video_frames(video, ffmpeg, 1.5)
        if expected == "black":
            assert black_intervals
        else:
            assert frozen_intervals
            with pytest.raises(RuntimeError, match="black or frozen"):
                _validate_video_source(video, shutil.which("ffprobe"), ffmpeg)


def _parse_srt_seconds(timestamp: str) -> float:
    hours, minutes, seconds = timestamp.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


@pytest.mark.integration
def test_script_job_produces_valid_video_and_required_metadata(tmp_path: Path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg/FFprobe are required for media integration tests.")
    if not shutil.which("say") and not shutil.which("espeak-ng"):
        pytest.skip("Install macOS say or espeak-ng for the local TTS integration test.")

    job_id = str(uuid.uuid4())
    result = render_script_job(
        job_id,
        {
            "script": (
                "Solar panels capture sunlight and convert it into electricity. "
                "A solar-powered home stores clean energy in a battery for evening use. "
                "Across the landscape, wind turbines and solar farms reduce carbon emissions."
            ),
            "voice": "en-IN-female",
            "aspect_ratios": ["16:9", "9:16", "1:1"],
            "style": "illustrated clean-energy graphics",
            "avoid": [],
            "captions": {"burn_in": True, "srt": True},
        },
        tmp_path / job_id,
    )

    output = tmp_path / job_id
    assert result["status"] == "succeeded"
    assert all(
        (output / filename).is_file()
        for filename in (
            "video_16_9.mp4", "video_9_16.mp4", "video_1_1.mp4", "video-qa.json",
        )
    )
    video_qa = json.loads((output / "video-qa.json").read_text(encoding="utf-8"))
    assert video_qa["status"] == "passed"
    assert set(video_qa["outputs"]) == {
        "video_16_9.mp4", "video_9_16.mp4", "video_1_1.mp4",
    }
    assert all(
        output_qa["video"]["pixel_format"] == "yuv420p"
        and output_qa["video"]["frame_rate"] == "30/1"
        and not output_qa["black_intervals"]
        and not output_qa["frozen_intervals"]
        for output_qa in video_qa["outputs"].values()
    )
    subtitle_text = (output / "captions.srt").read_text(encoding="utf-8")
    assert subtitle_text.count("-->") == 3
    timeline = json.loads((output / "timeline.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert timeline["scenes"][0]["start"] == 0
    assert timeline["scenes"][-1]["end"] == pytest.approx(result["duration_seconds"], abs=0.1)
    assert all(scene["source"]["type"] == "procedural-offline-visual" for scene in timeline["scenes"])
    assert all(scene["is_ai_generated"] is False for scene in timeline["scenes"])
    assert len({scene["clip"] for scene in timeline["scenes"]}) == len(timeline["scenes"])
    assert [
        f"{kind.replace('-', ' ')} illustration" in scene["selection_reason"]
        for kind, scene in zip(("solar-panels", "solar-home", "solar-landscape"),
                               timeline["scenes"])
    ] == [True, True, True]
    assert all("not stock footage" in scene["selection_reason"] for scene in timeline["scenes"])
    for index, scene in enumerate(timeline["scenes"]):
        assert scene["end"] > scene["start"]
        assert f"{index + 1}\n" in subtitle_text
        assert f"{scene['narration_text']}\n" in subtitle_text
        assert scene["start"] == pytest.approx(
            0 if index == 0 else timeline["scenes"][index - 1]["end"], abs=0.01
        )
    subtitle_cues = [
        block.splitlines()[1].split(" --> ")
        for block in subtitle_text.strip().split("\n\n")
    ]
    assert len(subtitle_cues) == len(timeline["scenes"])
    for (start, end), scene in zip(subtitle_cues, timeline["scenes"]):
        assert _parse_srt_seconds(start) == pytest.approx(scene["start"], abs=0.002)
        assert _parse_srt_seconds(end) == pytest.approx(scene["end"], abs=0.002)
    assert "not evidence of commercial B-roll rights" in (
        timeline["scenes"][0]["source"]["licence_evidence"]
    )
    assert any("not stock B-roll" in warning for warning in manifest["warnings"])
    assert "not stock B-roll" in manifest["licence_rights"]["status"]
    assert manifest["cost_estimate"]["amount"] is None
    scene_artwork_crops = []
    video_path = output / "video_16_9.mp4"
    for scene_index in (0, 1, 2):
        scene = timeline["scenes"][scene_index]
        midpoint = (scene["start"] + scene["end"]) / 2
        frame_bytes = subprocess.run(
            [
                shutil.which("ffmpeg"), "-v", "error", "-i", str(video_path),
                "-ss", str(midpoint), "-frames:v", "1", "-f", "image2pipe",
                "-vcodec", "png", "-",
            ],
            check=True,
            capture_output=True,
        ).stdout
        frame = Image.open(BytesIO(frame_bytes)).convert("RGB")
        scene_artwork_crops.append(
            frame.crop((150, 300, 1770, 820)).resize((320, 104))
        )
    for crop in scene_artwork_crops[1:]:
        difference = ImageChops.difference(scene_artwork_crops[0], crop)
        mean_difference = sum(ImageStat.Stat(difference).mean) / 3
        assert mean_difference > 10
    probe = json.loads(subprocess.run(
        [
            shutil.which("ffprobe"), "-v", "error", "-show_entries",
            "stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,duration",
            "-show_entries", "format=duration", "-of", "json",
            str(output / "video_16_9.mp4"),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout)
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in probe["streams"] if stream["codec_type"] == "audio")
    assert (video["codec_name"], video["width"], video["height"], video["r_frame_rate"]) == (
        "h264", 1920, 1080, "30/1",
    )
    assert audio["codec_name"] == "aac"
    assert float(video["duration"]) == pytest.approx(result["duration_seconds"], abs=0.15)
    assert float(audio["duration"]) == pytest.approx(result["duration_seconds"], abs=0.15)


@pytest.mark.integration
def test_mock_pexels_asset_is_rendered_with_creator_and_license_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg/FFprobe are required for media integration tests.")
    if not shutil.which("say") and not shutil.which("espeak-ng"):
        pytest.skip("Install macOS say or espeak-ng for the local TTS integration test.")

    source = tmp_path / "mock-source.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:r=15:d=0.5",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    corrupt_source = tmp_path / "corrupt-source.mp4"
    corrupt_source.write_bytes(b"not a valid video")
    asset = FootageAsset(
        asset_id="mock-video-1",
        file_url="https://videos.pexels.com/video-files/mock/video.mp4",
        source_url="https://www.pexels.com/video/sunset-over-water-1/",
        width=320,
        height=180,
        duration_seconds=0.5,
        creator_name="Mock Creator",
        creator_url="https://www.pexels.com/@mock-creator/",
        result_rank=1,
    )
    corrupt_asset = FootageAsset(
        asset_id="mock-video-corrupt",
        file_url="https://videos.pexels.com/video-files/mock/corrupt.mp4",
        source_url="https://www.pexels.com/video/broken-sample-2/",
        width=320,
        height=180,
        duration_seconds=0.5,
        creator_name="Mock Creator",
        creator_url="https://www.pexels.com/@mock-creator/",
        result_rank=1,
    )
    irrelevant_asset = FootageAsset(
        asset_id="mock-video-irrelevant",
        file_url="https://videos.pexels.com/video-files/mock/irrelevant.mp4",
        source_url="https://www.pexels.com/video/mountain-forest-3/",
        width=320,
        height=180,
        duration_seconds=0.5,
        creator_name="Mock Creator",
        creator_url="https://www.pexels.com/@mock-creator/",
        result_rank=1,
    )
    avoided_asset = FootageAsset(
        asset_id="mock-video-avoided",
        file_url="https://videos.pexels.com/video-files/mock/avoided.mp4",
        source_url="https://www.pexels.com/video/city-skyline-4/",
        width=320,
        height=180,
        duration_seconds=0.5,
        creator_name="Mock Creator",
        creator_url="https://www.pexels.com/@mock-creator/",
        result_rank=1,
    )

    class MockFootageProvider:
        def __init__(self):
            self.closed = False
            self.downloaded_ids = []
            self.sunset_searches = 0

        def search(self, query, orientation, target_dimensions):
            assert orientation == "landscape"
            assert target_dimensions == (1920, 1080)
            if query == "sunset over water":
                self.sunset_searches += 1
                if self.sunset_searches > 1:
                    return [asset]
                return [irrelevant_asset, asset]
            if query == "broken sample":
                return [corrupt_asset]
            assert query == "city skyline"
            return [avoided_asset]

        def download(self, selected_asset, destination):
            self.downloaded_ids.append(selected_asset.asset_id)
            if selected_asset == asset:
                shutil.copyfile(source, destination)
            elif selected_asset == corrupt_asset:
                shutil.copyfile(corrupt_source, destination)
            else:
                raise AssertionError("Rejected candidate should not be downloaded.")

        def close(self):
            self.closed = True

    mock_provider = MockFootageProvider()
    monkeypatch.setattr("app.pipeline.create_footage_provider", lambda: mock_provider)
    job_id = str(uuid.uuid4())
    output = tmp_path / job_id
    result = render_script_job(
        job_id,
        {
            "scenes": [
                {"text": "A calm evening.", "visual_hint": "sunset over water"},
                {"text": "A broken source.", "visual_hint": "broken sample"},
                {"text": "A city skyline.", "visual_hint": "city skyline"},
                {"text": "A second sunset.", "visual_hint": "sunset over water"},
            ],
            "aspect_ratios": ["16:9"],
            "avoid": ["city"],
            "captions": {"burn_in": False, "srt": True},
        },
        output,
    )

    timeline = json.loads((output / "timeline.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    (
        selected_scene,
        corrupt_fallback_scene,
        avoided_fallback_scene,
        repeated_fallback_scene,
    ) = timeline["scenes"]
    assert result["status"] == "succeeded"
    assert selected_scene["source"]["url"] == asset.source_url
    assert selected_scene["source"]["download_url"] == asset.file_url
    assert selected_scene["source"]["creator"]["name"] == "Mock Creator"
    assert selected_scene["source"]["creator"]["url"] == asset.creator_url
    assert selected_scene["source"]["licence_reference"] == "https://www.pexels.com/license/"
    assert selected_scene["source"]["attribution"] == "Video by Mock Creator on Pexels"
    assert selected_scene["source"]["licence_evidence"].startswith(
        "Provider source and creator URLs recorded"
    )
    assert selected_scene["is_procedurally_generated"] is False
    assert selected_scene["title_keyword_overlap"] == 1
    assert selected_scene["relevance_score"] is None
    assert corrupt_fallback_scene["is_procedurally_generated"] is True
    assert "failed media validation" in corrupt_fallback_scene["fallback_reason"]
    assert avoided_fallback_scene["is_procedurally_generated"] is True
    assert "avoid term(s): city" in avoided_fallback_scene["fallback_reason"]
    assert repeated_fallback_scene["is_procedurally_generated"] is True
    assert "already used" in repeated_fallback_scene["fallback_reason"]
    assert any("failed media validation" in warning for warning in manifest["warnings"])
    assert any("rejected Pexels candidate mock-video-irrelevant" in warning
               for warning in manifest["warnings"])
    assert mock_provider.downloaded_ids == ["mock-video-1", "mock-video-corrupt"]
    assert manifest["licence_rights"]["asset_count"] == 1
    assert "not independently verified" in manifest["licence_rights"]["status"]
    assert result["attribution_notice"]["url"] == "https://www.pexels.com/"
    assert mock_provider.closed


@pytest.mark.integration
def test_audio_validation_accepts_supported_formats_and_rejects_bad_inputs(tmp_path: Path):
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg/FFprobe are required for audio validation tests.")

    for extension, codec in (
        (".mp3", "libmp3lame"),
        (".wav", "pcm_s16le"),
        (".m4a", "aac"),
    ):
        audio_path = tmp_path / f"voice{extension}"
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=1",
                "-c:a",
                codec,
                str(audio_path),
            ],
            check=True,
        )
        info = validate_audio(audio_path, ffprobe=ffprobe, ffmpeg=ffmpeg)
        assert info.duration_seconds == pytest.approx(1, abs=0.15)
        with pytest.raises(AudioInputError, match="duration exceeds"):
            validate_audio(
                audio_path,
                ffprobe=ffprobe,
                ffmpeg=ffmpeg,
                max_duration_seconds=0,
            )

    silent_path = tmp_path / "silent.wav"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono",
            "-t",
            "1",
            str(silent_path),
        ],
        check=True,
    )
    with pytest.raises(AudioInputError, match="silent"):
        validate_audio(silent_path, ffprobe=ffprobe, ffmpeg=ffmpeg)

    corrupt_path = tmp_path / "corrupt.mp3"
    corrupt_path.write_bytes(b"not an audio stream")
    with pytest.raises(AudioInputError, match="corrupt"):
        validate_audio(corrupt_path, ffprobe=ffprobe, ffmpeg=ffmpeg)
