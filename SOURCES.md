# Sources and licences

This file must be updated with every model, API, library, dataset, and media provider actually used.

## Current scaffold
- FastAPI — https://github.com/fastapi/fastapi — MIT licence.
- Pydantic — https://github.com/pydantic/pydantic — MIT licence.
- Uvicorn — https://github.com/encode/uvicorn — BSD-3-Clause licence.
- pytest — https://github.com/pytest-dev/pytest — MIT licence.
- HTTPX — https://github.com/encode/httpx — BSD-3-Clause licence.
- boto3 — https://github.com/boto/boto3 — Apache-2.0 licence; optional S3
  compatible object storage adapter. No live object-store endpoint was used for
  the preserved verification evidence.
- Pillow — https://github.com/python-pillow/Pillow — HPND licence; used for
  deterministic in-frame caption overlays.
- faster-whisper — https://github.com/SYSTRAN/faster-whisper — MIT licence;
  local timestamped ASR. Python package version is recorded in each audio job
  manifest.
- FFmpeg — https://ffmpeg.org/legal.html — build configuration/licence must be checked for the exact distributed build.

## Media / models
No stock-footage provider, model, dataset, or cloud TTS/transcription service
has been used for the preserved evidence runs to date. The default renderer
creates original topic-aware illustrations locally (not stock footage or
commercial B-roll) and uses the installed system speech engine.
The audio pipeline uses faster-whisper locally; the selected checkpoint is
downloaded from the model source on first audio use and cached. The default
`base` model is the CTranslate2 conversion at
[Systran/faster-whisper-base](https://huggingface.co/Systran/faster-whisper-base)
of [OpenAI Whisper base](https://huggingface.co/openai/whisper-base). Check the
upstream model-card terms and deployment requirements before distributing model
weights. Audio data is not submitted to a remote transcription API; first-run
model downloads do require network access.

An opt-in Pexels API adapter is implemented but disabled by default and has not
been used with an API key. Enable it only after confirming Pexels permits this
product-specific API use. Review the current
[Pexels API guidelines](https://www.pexels.com/api/documentation/) and
[Pexels licence](https://www.pexels.com/license/). The API guidelines require a
prominent Pexels link, creator credits where possible, and prohibit replicating
Pexels' core functionality. The licence grants broad use of content subject to
prohibited uses, but Pexels does not warrant that all individual third-party
consents or rights have been obtained. These terms do not constitute a legal
determination that this product's API use or any individual asset is permitted.

- espeak-ng — https://github.com/espeak-ng/espeak-ng — GPL-3.0-or-later; installed
  from the base distribution package in Docker. Confirm distribution obligations
  for the final deployment image.
- macOS `say` — Apple-provided system speech command used only when available on
  the developer's macOS host. It is not bundled in the container.
- FFmpeg / FFprobe — local executables. Verify the licence configuration of the
  actual Docker image and host build before distributing a production service.

The preserved rendered visual assets are procedural abstract graphics. The
optional adapter records a selected asset's Pexels video page, creator,
attribution, and licence reference in the timeline and manifest; it does not
independently verify model releases, property rights, or product API permission.
