from dataclasses import replace

import httpx
import pytest

from app.footage import (
    FootageAsset,
    FootageProviderError,
    PexelsFootageProvider,
    assess_asset_keywords,
    create_footage_provider,
)


def test_candidate_keyword_gate_ranks_by_title_overlap_and_enforces_avoid_terms():
    asset = FootageAsset(
        asset_id="123",
        file_url="https://videos.pexels.com/video-files/123/1080p.mp4",
        source_url="https://www.pexels.com/video/city-skyline-at-night-123/",
        width=1920,
        height=1080,
        duration_seconds=8,
        creator_name="Creator",
        creator_url="https://www.pexels.com/@creator/",
        result_rank=2,
    )

    score, reason = assess_asset_keywords(asset, "city skyline at night", [])

    assert score == 1
    assert reason is None
    assert assess_asset_keywords(asset, "ocean wildlife", [])[1].startswith(
        "Candidate title keyword overlap"
    )
    assert assess_asset_keywords(asset, "city skyline", ["city skyline"])[1] == (
        "Candidate title matches avoid term(s): city skyline."
    )


def test_candidate_keyword_gate_fails_closed_when_title_has_no_signal():
    asset = FootageAsset(
        asset_id="123",
        file_url="https://videos.pexels.com/video-files/123/1080p.mp4",
        source_url="https://www.pexels.com/video/123/",
        width=1920,
        height=1080,
        duration_seconds=8,
        creator_name="Creator",
        creator_url="https://www.pexels.com/@creator/",
        result_rank=1,
    )

    assert assess_asset_keywords(asset, "city skyline", []) == (
        None,
        "Candidate or scene has insufficient descriptive keywords.",
    )


def test_pexels_search_parses_provenance_and_sends_api_key():
    def respond(request):
        assert request.url.path == "/videos/search"
        assert request.headers["Authorization"] == "test-secret"
        assert request.url.params["query"] == "city skyline"
        assert request.url.params["orientation"] == "landscape"
        return httpx.Response(200, json={
            "videos": [{
                "id": 123,
                "url": "https://www.pexels.com/video/city-123/",
                "duration": 8,
                "user": {"name": "Creator", "url": "https://www.pexels.com/@creator/"},
                "video_files": [
                    {
                        "file_type": "video/mp4",
                        "width": 1920,
                        "height": 1080,
                        "link": "https://videos.pexels.com/video-files/123/1080p.mp4",
                    },
                    {
                        "file_type": "video/mp4",
                        "width": 1920,
                        "height": 1080,
                        "link": "https://untrusted.example/video.mp4",
                    },
                ],
            }],
        })

    provider = PexelsFootageProvider(
        "test-secret",
        transport=httpx.MockTransport(respond),
    )
    try:
        assets = provider.search("city skyline", "landscape", (1920, 1080))
    finally:
        provider.close()

    assert assets == [
        FootageAsset(
            asset_id="123",
            file_url="https://videos.pexels.com/video-files/123/1080p.mp4",
            source_url="https://www.pexels.com/video/city-123/",
            width=1920,
            height=1080,
            duration_seconds=8,
            creator_name="Creator",
            creator_url="https://www.pexels.com/@creator/",
            result_rank=1,
        )
    ]
    assert assets[0].attribution == "Video by Creator on Pexels"


def test_pexels_download_enforces_host_and_size_limit(tmp_path):
    requested = []

    def respond(request):
        requested.append(str(request.url))
        return httpx.Response(200, content=b"too-large")

    provider = PexelsFootageProvider(
        "test-secret",
        max_download_bytes=4,
        transport=httpx.MockTransport(respond),
    )
    asset = FootageAsset(
        asset_id="123",
        file_url="https://videos.pexels.com/video-files/123/1080p.mp4",
        source_url="https://www.pexels.com/video/city-123/",
        width=1920,
        height=1080,
        duration_seconds=8,
        creator_name="Creator",
        creator_url="https://www.pexels.com/@creator/",
        result_rank=1,
    )
    destination = tmp_path / "source.mp4"
    try:
        with pytest.raises(FootageProviderError, match="download limit"):
            provider.download(asset, destination)
        with pytest.raises(FootageProviderError, match="non-approved"):
            provider.download(
                replace(asset, file_url="https://untrusted.example/file.mp4"),
                destination,
            )
    finally:
        provider.close()

    assert requested == ["https://videos.pexels.com/video-files/123/1080p.mp4"]
    assert not destination.exists()


def test_pexels_search_errors_do_not_expose_api_key():
    def respond(request):
        return httpx.Response(429, json={"error": "rate limited"})

    provider = PexelsFootageProvider(
        "test-secret",
        transport=httpx.MockTransport(respond),
    )
    try:
        with pytest.raises(FootageProviderError, match="HTTP 429") as exc_info:
            provider.search("nature", "landscape", (1920, 1080))
    finally:
        provider.close()
    assert "test-secret" not in str(exc_info.value)


def test_pexels_provider_is_opt_in_and_requires_permission(monkeypatch):
    monkeypatch.setenv("FOOTAGE_PROVIDER", "pexels")
    monkeypatch.setenv("PEXELS_API_KEY", "test-secret")
    monkeypatch.setenv("PEXELS_USE_APPROVED", "false")
    with pytest.raises(RuntimeError, match="permission"):
        create_footage_provider()

    monkeypatch.setenv("PEXELS_USE_APPROVED", "true")
    monkeypatch.delenv("PEXELS_API_KEY")
    with pytest.raises(RuntimeError, match="PEXELS_API_KEY"):
        create_footage_provider()


def test_offline_provider_remains_the_default(monkeypatch):
    monkeypatch.delenv("FOOTAGE_PROVIDER", raising=False)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    assert create_footage_provider() is None
