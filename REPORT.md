# Engineering assessment implementation report

## Current result

The repository has a working local vertical slice for text/script and audio
inputs. The API creates a persistent SQLite job, applies idempotency, and
returns `202` plus a status URL. Text requests use a locally installed speech
synthesizer to create sentence-sized audio and measure durations. Audio requests
accept MP3/WAV/M4A uploads or HTTPS URLs restricted to an explicit host
allowlist, validate the media, and use local `faster-whisper` transcription;
the original audio is retained and Whisper timestamps drive transcript captions.
Abstract graphics remain the default. An opt-in Pexels provider can search,
download, and render footage with per-scene provenance and an explicit graphics
fallback, but remains disabled pending product-specific API permission. The
pipeline validates MP4 streams with FFprobe and writes a provenance manifest.
Local download URLs are returned. SQLite stores requests, job state, progress,
output result links, and terminal errors. Queued/running tasks are re-queued on
application startup. JSON errors use a common envelope, and the API echoes a
correlation ID in its response header. The API documentation endpoint is
versioned under `/api/v1/`.

The default and preserved evidence runs use generated graphics, not B-roll.
The provider code has only been validated using mocked responses; no API key or
live Pexels assets have been used. The voice label in the request is recorded
but does not select a voice. Real Whisper transcription and an audio-to-video
flow have been exercised on locally generated synthetic speech; no
representative user recording, accent/language accuracy, or human transcript
quality evaluation has been run.

## Design alternatives and decisions

The starter already used FastAPI and SQLite. Those components were retained to
keep the submission understandable and avoid introducing a broker or external
service before there is a verified need. The `Idempotency-Key` has a SQLite
unique constraint. Exact request replays return the same job; a different
payload on the same key is rejected with a conflict. A failed render is stored
as failed rather than returning a partial result as success.

For local automated checks, system TTS (espeak-ng in the container; macOS `say`
on the current host), procedural visuals, and mocked Pexels responses avoid API
keys or unverified media. Sentence-level TTS chunks provide measured segment
durations, unlike guessed timing proportional to text length. The timeline
exposes that timing source and uses null relevance scores instead of invented
confidence. The optional provider records the video page, creator, attribution,
and licence reference; it rejects unapproved hosts and oversized downloads,
avoids repeated IDs within a job, and gates candidates using title-URL keyword
overlap plus title-text avoid terms. This is a lexical metadata heuristic, not
semantic relevance or visual moderation. An explicit graphics fallback handles
search/download/corrupt-media failures and candidates without title signal.

For audio input, `faster-whisper` runs locally and its measured segment times
are retained for transcript cues; the submitted recording, rather than
synthesized replacement speech, is rendered into the output. Audio checks
reject unsupported, corrupt, silent, empty, and overlong files. URL inputs are
HTTPS-only, constrained to configured allowed hosts, and bounded by timeout
and download size. The first model use requires fetching model weights into the
configured cache. A compatibility pin for PyAV (`av>=11,<19`) avoids an API
incompatibility with the selected faster-whisper version.

Pexels' published API guidelines require prominent Pexels attribution and
prohibit replicating its core functionality. The general content licence allows
commercial uses subject to prohibited uses, but Pexels disclaims responsibility
for individual third-party permissions. Whether this script-to-video product's
API use is permitted has not been confirmed. The implementation therefore
defaults to offline mode and refuses Pexels requests unless the local
`PEXELS_USE_APPROVED=true` gate is explicitly set after confirmation. This flag
does not itself establish legal permission.

FFmpeg is responsible for encoding and Pillow for caption overlays. The output
is H.264/AAC at 30 fps with 48 kHz audio; the renderer validates codec,
dimensions, frame rate, pixel format, sample rate, and audio/video track timing.
It emits `video-qa.json`, including FFmpeg black-interval and freeze detection
for each final ratio, and rejects outputs that exceed the configured thresholds.
Integration tests cover all three offline ratios, deliberately black/frozen
fixtures, and a mocked footage render/fallback. A real all-ratio generated-
graphics run is preserved under `evidence/acceptance/m4/`.

Only one optional footage source is implemented; a second independently
approved source is still needed for Advanced. SQLite/filesystem storage remains
the local default. An optional S3-compatible adapter uploads and streams job
artifacts, and Compose accepts external object-store configuration;
distributed job claiming, callbacks, and second-provider failover remain
future work.

## Verification and measurements

The latest full host test run completed with **55 passed, 1 skipped** in
**563.29 seconds** using an external Python 3.13 virtual environment and
dependencies from `requirements.txt`. The skipped test is the opt-in real
Whisper integration test; no real model test was run in this verification. The
full Docker test profile completed with **55 passed, 1 skipped** in **171.21
seconds** using the Python 3.12 image and filesystem storage. A live MinIO/S3
endpoint was unavailable (Docker Hub no longer serves `minio/minio`; Quay
returned 401), so no live object-store integration result is claimed. Both
test runs exercised video QA, including all-ratio output checks and synthetic
black/frozen fixtures; the host run also includes timeline invariant and
object-storage adapter unit tests. Pexels responses remain mocked; no Pexels
API request or credential was used.

A real local pipeline run generated all three ratios and saved per-output
FFprobe and QA data in `evidence/acceptance/m4/`. The latest Compose API image
passed `/health` and `/ready`, processed a real script job, and served its MP4,
captions, timeline, manifest, and QA report. Port 8000 was already occupied,
so the smoke test used host port 18000; artifacts are under
`evidence/acceptance/m8/run-004/`. This is local container evidence, not a
hosted deployment or review-period uptime proof.

Pytest emitted one Starlette deprecation warning about the current
`starlette.testclient`/HTTPX integration; it did not affect the passing results.

The checked-in OpenAPI YAML parses as 3.1.0, and a test compares documented
paths and HTTP methods with the running FastAPI route inventory. The Compose
image and test profile were built and run successfully. The default `docker
compose up` API port mapping could not be used because host port 8000 was
occupied; `docker compose run -p 18000:8000` verified the API container instead.

The test runtime is not a production render-time benchmark. No human relevance
ratings, 10-input evaluation, concurrency/load test, production cost, per-minute
cost, or 85% high-relevance measurement exists. The manifest reports local
compute cost as unknown, not zero. No claim of measured human relevance is made.

## Readiness and limitations

The present implementation verifies job acceptance/retrieval, idempotency,
local rendering, requested ratio generation, downloadable outputs, and batch
submission for script jobs. Video QA now checks generated output streams,
black/frozen intervals, and track timing; visual crop quality remains manually
unreviewed. Mocked tests exercise footage metadata, encoding, and fallback, but
do not establish live media rights, product API permission, semantic scene
relevance, safe selection of real-world footage, or production availability.
There is no hosted deployment, reviewer access, rate limiting, authentication,
verified live S3-compatible integration, object expiry policy, callback signing,
second-source failover, multi-process worker coordination, or resource limit
around total concurrent render jobs. These are material assessment gaps, not
cosmetic follow-ups.

The official assessment brief and eight-tab Submission Workbook are present in
`docs/` and have been reviewed. The traceability matrix is grounded in those
sources. The original workbook remains unmodified. The submitted test pack
includes one generated-graphics smoke input and one batch-run input, not the
required ten varied evaluation inputs. No human ratings or workbook values
have been fabricated.

## Risks and next steps

1. Obtain Pexels' product-specific API permission or use independently verified
   local/licensed assets; then validate live footage, add a second provider,
   semantic relevance, visual avoid enforcement, and safety controls. The
   current title-keyword gate is only a conservative metadata heuristic.
2. Evaluate audio uploads/URLs, transcription quality across representative
   recordings, languages and accents; implement request-configurable TTS.
3. Configure a reachable MinIO/S3 endpoint and verify live object
   uploads/downloads; add reliable worker coordination/retries and verify the
   default-port Compose workflow on a host where port 8000 is free.
4. Complete the ten-input evaluation without fabricating human ratings, save
   every outcome, and report measured relevance/latency/cost honestly.
5. Complete and independently check the workbook, demo plan/recording,
   deployment/rate limiting, and reviewer access.
