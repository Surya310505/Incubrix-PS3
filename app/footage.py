from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

PEXELS_LICENSE_URL = "https://www.pexels.com/license/"
PEXELS_API_URL = "https://api.pexels.com/videos/search"
MIN_TITLE_KEYWORD_OVERLAP = 0.34
_IGNORED_KEYWORDS = {
    "a", "about", "after", "an", "and", "are", "at", "be", "by", "for",
    "from", "in", "into", "is", "of", "on", "or", "over", "the", "this",
    "to", "through", "video", "with",
}


class FootageProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class FootageAsset:
    asset_id: str
    file_url: str
    source_url: str
    width: int
    height: int
    duration_seconds: float | None
    creator_name: str
    creator_url: str
    result_rank: int

    @property
    def attribution(self) -> str:
        return f"Video by {self.creator_name} on Pexels"


def assess_asset_keywords(
    asset: FootageAsset,
    query: str,
    avoid_terms: list[str],
) -> tuple[float | None, str | None]:
    """Apply a conservative, title-only keyword gate; this is not visual moderation."""
    title = unquote(urlparse(asset.source_url).path).lower()
    title_tokens = {
        token for token in re.findall(r"[a-z0-9]+", title)
        if len(token) > 1 and not token.isdigit() and token not in _IGNORED_KEYWORDS
    }
    query_tokens = [
        token for token in re.findall(r"[a-z0-9]+", query.lower())
        if len(token) > 1 and not token.isdigit() and token not in _IGNORED_KEYWORDS
    ][:6]
    if not title_tokens or not query_tokens:
        return None, "Candidate or scene has insufficient descriptive keywords."

    matched_avoid_terms = []
    for term in avoid_terms:
        term_tokens = {
            token for token in re.findall(r"[a-z0-9]+", term.lower())
            if len(token) > 1 and not token.isdigit()
        }
        if term_tokens and term_tokens.issubset(title_tokens):
            matched_avoid_terms.append(term)
    if matched_avoid_terms:
        return None, f"Candidate title matches avoid term(s): {', '.join(matched_avoid_terms)}."

    overlap = len(title_tokens & set(query_tokens)) / len(query_tokens)
    if overlap < MIN_TITLE_KEYWORD_OVERLAP:
        return overlap, (
            f"Candidate title keyword overlap {overlap:.2f} is below "
            f"the {MIN_TITLE_KEYWORD_OVERLAP:.2f} minimum."
        )
    return overlap, None


class PexelsFootageProvider:
    def __init__(
        self,
        api_key: str,
        timeout_seconds: float = 30,
        max_download_bytes: int = 100_000_000,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("PEXELS_API_KEY must not be empty.")
        self._client = httpx.Client(
            headers={"Authorization": api_key},
            timeout=timeout_seconds,
            follow_redirects=False,
            transport=transport,
        )
        self._max_download_bytes = max_download_bytes

    def close(self) -> None:
        self._client.close()

    def search(
        self,
        query: str,
        orientation: str,
        target_dimensions: tuple[int, int],
    ) -> list[FootageAsset]:
        normalized_query = query.strip()[:500]
        if not normalized_query:
            raise FootageProviderError("Pexels search query was empty.")
        if orientation not in {"landscape", "portrait", "square"}:
            raise ValueError(f"Unsupported Pexels orientation: {orientation}")

        try:
            response = self._client.get(
                PEXELS_API_URL,
                params={
                    "query": normalized_query,
                    "orientation": orientation,
                    "size": "medium",
                    "per_page": 10,
                },
            )
            if response.status_code != 200:
                raise FootageProviderError(
                    f"Pexels search failed with HTTP {response.status_code}."
                )
            response.raise_for_status()
            payload = response.json()
        except FootageProviderError:
            raise
        except httpx.HTTPStatusError as exc:
            raise FootageProviderError(
                f"Pexels search failed with HTTP {exc.response.status_code}."
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise FootageProviderError(
                f"Pexels search request failed ({type(exc).__name__})."
            ) from exc

        videos = payload.get("videos") if isinstance(payload, dict) else None
        if not isinstance(videos, list):
            raise FootageProviderError("Pexels search returned an invalid response.")

        assets = []
        for rank, video in enumerate(videos, start=1):
            if not isinstance(video, dict):
                continue
            files = video.get("video_files")
            if not isinstance(files, list):
                continue
            usable_files = [
                item for item in files
                if isinstance(item, dict)
                and item.get("file_type") == "video/mp4"
                and isinstance(item.get("width"), int)
                and isinstance(item.get("height"), int)
                and item["width"] > 0
                and item["height"] > 0
                and self._is_download_url(item.get("link"))
            ]
            if not usable_files:
                continue
            target_width, target_height = target_dimensions
            selected_file = min(
                usable_files,
                key=lambda item: (
                    abs(item["width"] / item["height"] - target_width / target_height),
                    abs(item["width"] * item["height"] - target_width * target_height),
                    -(item["width"] * item["height"]),
                ),
            )
            user = video.get("user") if isinstance(video.get("user"), dict) else {}
            source_url = video.get("url")
            creator_url = user.get("url")
            if not self._is_pexels_page_url(source_url) or not self._is_pexels_page_url(creator_url):
                continue
            asset_id = video.get("id")
            if asset_id is None:
                continue
            duration = video.get("duration")
            assets.append(FootageAsset(
                asset_id=str(asset_id),
                file_url=selected_file["link"],
                source_url=source_url,
                width=selected_file["width"],
                height=selected_file["height"],
                duration_seconds=duration if isinstance(duration, (int, float)) else None,
                creator_name=str(user.get("name") or "Unknown creator"),
                creator_url=creator_url,
                result_rank=rank,
            ))
        return assets

    def download(self, asset: FootageAsset, destination: Path) -> None:
        if not self._is_download_url(asset.file_url):
            raise FootageProviderError("Pexels returned a non-approved video download URL.")

        destination.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            with self._client.stream("GET", asset.file_url) as response:
                if response.status_code != 200:
                    raise FootageProviderError(
                        f"Pexels video download failed with HTTP {response.status_code}."
                    )
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > self._max_download_bytes:
                    raise FootageProviderError("Pexels video exceeds the configured download limit.")
                with destination.open("wb") as output:
                    for chunk in response.iter_bytes():
                        written += len(chunk)
                        if written > self._max_download_bytes:
                            raise FootageProviderError("Pexels video exceeds the configured download limit.")
                        output.write(chunk)
        except FootageProviderError:
            destination.unlink(missing_ok=True)
            raise
        except (httpx.HTTPError, OSError, ValueError) as exc:
            destination.unlink(missing_ok=True)
            raise FootageProviderError(
                f"Pexels video download failed ({type(exc).__name__})."
            ) from exc
        if written == 0:
            destination.unlink(missing_ok=True)
            raise FootageProviderError("Pexels returned an empty video file.")

    @staticmethod
    def _is_download_url(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        parsed = urlparse(value)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "videos.pexels.com"
            and parsed.username is None
            and parsed.password is None
        )

    @staticmethod
    def _is_pexels_page_url(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        parsed = urlparse(value)
        return (
            parsed.scheme == "https"
            and parsed.hostname in {"pexels.com", "www.pexels.com"}
            and parsed.username is None
            and parsed.password is None
        )


def create_footage_provider() -> PexelsFootageProvider | None:
    provider_name = os.getenv("FOOTAGE_PROVIDER", "offline").strip().lower()
    if provider_name == "offline":
        return None
    if provider_name != "pexels":
        raise RuntimeError(f"Unsupported FOOTAGE_PROVIDER: {provider_name}")
    if os.getenv("PEXELS_USE_APPROVED", "").strip().lower() != "true":
        raise RuntimeError(
            "Pexels use is disabled until product-specific API permission is confirmed; "
            "set PEXELS_USE_APPROVED=true only after obtaining that confirmation."
        )
    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key:
        raise RuntimeError("PEXELS_API_KEY is required when FOOTAGE_PROVIDER=pexels.")
    return PexelsFootageProvider(api_key)
