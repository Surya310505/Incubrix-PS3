# IncuBrix PS3 — Script and Voiceover to B-roll Video Engine

> **Verified scope is partial.** This repository has script/scene-list rendering
> and an audio-upload pipeline using locally configured faster-whisper
> transcription. Its offline fallback renders original topic-aware illustrations,
> not stock footage. An
> opt-in Pexels provider is implemented but remains disabled until Pexels
> confirms that this product's API use is permitted. Semantic relevance
> filtering, safety, deployment, and the official evaluation remain incomplete. See
> [requirements traceability](docs/requirements-traceability.md) before treating
> this as assessment-complete.

## Run the API

Docker Compose starts the API without requiring a checked-in `.env` file. Job
outputs use the filesystem by default. To use MinIO or another S3-compatible
service, set `OBJECT_STORE_ENDPOINT_URL`, `OBJECT_STORE_ACCESS_KEY_ID`, and
`OBJECT_STORE_SECRET_ACCESS_KEY`; completed outputs are then uploaded to the
configured bucket and downloads are streamed from object storage.

```bash
docker compose up --build
```

The API is at <http://localhost:8000>; interactive docs are at
<http://localhost:8000/api/v1/docs>. To run the unit, local-media integration, and API
end-to-end tests in the Docker image without cloud credentials:

```bash
docker compose --profile test run --build --rm test
```

The offline test profile does not require an object-store service:

```bash
docker compose --profile test run --build --rm test
```

For local development, install FFmpeg, FFprobe, and either `espeak-ng` or a
platform `say` command, then:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
pytest -q
```

Local data defaults to the repository's `data/` directory and uses filesystem
storage unless `OBJECT_STORE_ENDPOINT_URL` is set. Docker data defaults to the
named `app_data` volume. `.env.example` lists all current application settings;
copy it to `.env` to configure an external S3-compatible endpoint.

## Create a clean submission archive

Run this from the project directory, choosing an output ZIP path outside the
project. The script omits virtual environments, caches, macOS metadata, local
`.env` secrets, SQLite/runtime `data/`, and generated local outputs while
retaining the checked-in reproducibility evidence under `evidence/`:

```bash
sh ./scripts/package_submission.sh /path/outside/project/incubrix-submission.zip
```

The script refuses to overwrite an existing archive. Review the archive
contents before submitting it.

## Optional Pexels footage provider

The default `FOOTAGE_PROVIDER=offline` setting makes no external media requests.
The Pexels adapter is opt-in and is gated by an explicit permission setting.
Pexels API guidelines prohibit copying or replicating Pexels' core functionality;
the project's video-generation use has not been confirmed as permitted. Do not
enable the provider until Pexels confirms product-specific API permission.

After confirmation, put the API key in the ignored local `.env` file (never
commit or paste it into source) and configure:

```dotenv
FOOTAGE_PROVIDER=pexels
PEXELS_API_KEY=your-local-key
PEXELS_USE_APPROVED=true
```

Compose passes these settings to the API container. For each scene the adapter
searches by its visual hint or narration, then requires at least 0.34 title-URL
keyword overlap, rejects title-URL matches to configured `avoid` terms, and
tries the highest-overlap downloadable, non-repeated result first. Candidates
without descriptive title keywords use the procedural visual fallback.
Selected clips record the Pexels video page, creator, creator page, attribution,
and Pexels licence reference. The overlap is a lexical heuristic, not a
semantic relevance score, and the page title cannot verify what a video depicts.
Job results and manifests link to Pexels and include creator attribution.
Individual model/property rights and visual safety remain unverified.

When footage is unavailable, the offline renderer creates original topic-aware
illustrations (including distinct solar-panel, solar-home, and clean-energy
landscape scenes) and applies gentle camera motion. These graphics are not stock
footage and are not represented as commercially licensed B-roll; commercial
suitability has not been assessed.

## Submit and retrieve a job

Every create request requires an `Idempotency-Key`. Reusing the same key and
payload returns the original job; reusing it with different content returns
`409 IDEMPOTENCY_KEY_REUSED`.

```bash
curl -X POST http://localhost:8000/api/v1/broll/jobs \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-001' \
  -d '{"script":"Saving money starts with a plan. Track every expense.","aspect_ratios":["16:9"],"captions":{"burn_in":true,"srt":true}}'
```

Poll `status_url` until the status is `succeeded` or `failed`. The result contains
download links for the requested MP4 ratios, `captions.srt` (when enabled),
`timeline.json`, `manifest.json`, and `video-qa.json`. A job is only reported as
succeeded after FFprobe checks H.264/AAC, dimensions, frame rate, pixel format,
audio rate, track duration, and synchronization. FFmpeg checks for black
intervals of at least 0.1 seconds and frozen intervals of at least 1 second.
These checks do not replace human review of crop quality.

All API errors use the `{"error":{"code","message","retryable","details"}}`
envelope. Current codes include `AUDIO_TOO_LARGE`, `EMPTY_AUDIO`,
`FILE_NOT_FOUND`, `HTTP_ERROR`, `IDEMPOTENCY_KEY_REQUIRED`,
`IDEMPOTENCY_KEY_REUSED`, `INTERNAL_ERROR`, `JOB_NOT_FOUND`, `NOT_READY`,
`PROCESSING_FAILED`, `UNSUPPORTED_AUDIO_FORMAT`, and `VALIDATION_ERROR`.

Supported input today: a non-empty `script` or a `scenes` array of text and
optional visual hints. Text-only input uses local TTS. For an existing
voiceover, upload MP3/WAV/M4A to `POST /api/v1/broll/jobs/audio`; the original
audio track is retained for the final MP4, and local Whisper timestamps drive
the transcript timeline and SRT captions:

```bash
curl -X POST http://localhost:8000/api/v1/broll/jobs/audio \
  -H 'Idempotency-Key: audio-demo-001' \
  -F 'audio_file=@voiceover.wav' \
  -F 'aspect_ratios=16:9' \
  -F 'language=en'
```

JSON job requests may alternatively provide an `audio_url`. URL ingestion is
HTTPS-only, requires an exact host in the comma-separated
`AUDIO_URL_ALLOWED_HOSTS` setting, rejects hosts resolving to non-public
addresses, and validates every redirect. URL and upload size are limited by
`MAX_AUDIO_UPLOAD_BYTES`; media must decode as audio-only MP3/WAV/M4A, be
non-empty and non-silent, and be no longer than `MAX_AUDIO_SECONDS`.

`faster-whisper` runs locally with `WHISPER_MODEL_SIZE=base`,
`WHISPER_DEVICE=cpu`, and `WHISPER_COMPUTE_TYPE=int8` by default. Its model
weights are downloaded on first use and cached in the persistent data volume
when using Compose. Audio is not sent to a transcription service; once the
model is cached, transcription itself runs locally. Model downloads therefore
require network access on first use. Whisper segment timestamps—not text length
or guessed timing—drive the transcript captions and scene timeline. Review
transcripts for accuracy before submission. Text-only input continues to use
the local system TTS engine; the `voice` field is recorded but does not select a
particular voice.

Current error codes also include `AUDIO_TOO_LARGE`, `EMPTY_AUDIO`, and
`UNSUPPORTED_AUDIO_FORMAT`.

## Batch runner

`scripts/batch_runner.py` accepts one request JSON file or a folder of `*.json`
request files. It submits each input, polls the job, saves the job response and
downloads every returned file into a per-input output directory:

```bash
python3 scripts/batch_runner.py examples/inputs ./batch-output --api http://localhost:8000
```

The runner uses a stable key based on the input path and content, writes
`failure.json` for failed submissions/jobs, continues to the next input, and
returns a non-zero exit code if any input fails.

## Implementation and boundaries

- `app/main.py`: FastAPI routes, validation, SQLite job state, idempotency,
  recovery of interrupted queued/running jobs, JSON logs and correlation IDs.
- `app/pipeline.py`: local TTS, sentence timing measured from per-sentence audio,
  SRT/timeline/manifest generation, optional footage rendering and explicit
  procedural fallback, encoding and FFprobe validation.
- `app/audio.py`: MP3/WAV/M4A validation, duration/volume checks, HTTPS
  allowlisted URL downloads, lazy local faster-whisper loading, and strictly
  validated transcript timestamps.
- `app/storage.py`: optional S3-compatible bucket setup, output uploads, and
  streamed downloads. Filesystem storage remains the default unless
  `OBJECT_STORE_ENDPOINT_URL` is configured.
- `app/footage.py`: opt-in Pexels API adapter with bounded downloads, provenance
  metadata, local environment-key configuration and mocked tests.
- `openapi.yaml`: checked-in OpenAPI 3.1 API contract.
- `docs/requirements-traceability.md`: status and remaining work for the
  supplied assessment requirements.

The offline visual generator is a deterministic fallback for exercising the
rendering path, not footage retrieval. Pexels is never the default and its use
must remain disabled until product-specific API permission is confirmed.
Timeline relevance scores are `null` rather than fabricated. No claim is made
that visuals are semantically relevant or human-rated. Unit and integration
tests use mocked provider responses and do not contact Pexels.

## Tests and evidence

`pytest -q` includes API tests, an FFmpeg/TTS integration test, and an API-to-file
end-to-end test. The test pack contains a reproducible generated-graphics smoke
artifact and explicitly does not represent the ten-input human evaluation.
Measured human relevance, production cost/throughput, and external asset licence
verification are pending. Keep failed runs and weak outputs in the evidence pack
when the full evaluation set is executed.

## Troubleshooting

- `No local TTS engine found`: install `espeak-ng` or configure `SAY_BINARY` for
  the local platform. The Docker image installs `espeak-ng`.
- `FFmpeg/FFprobe` errors: check `FFMPEG_BINARY` and `FFPROBE_BINARY`, and ensure
  the installed FFmpeg has the `libx264` encoder.
- A script job that fails is reported as `failed` with a JSON error; no
  success-shaped partial result is returned.
- The official brief and workbook are in `docs/`; see the source-grounded
  [traceability matrix](docs/requirements-traceability.md). The original
  workbook remains unmodified.
