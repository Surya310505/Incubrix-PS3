# Architecture

## Runtime flow

1. FastAPI validates a JSON script/scene-list or allowlisted audio-URL request,
   or receives an MP3/WAV/M4A multipart upload; all require `Idempotency-Key`.
2. The SQLite `jobs` table stores the canonical request, idempotency key,
   lifecycle state, progress, result, and error.
3. A background task marks the job running and invokes
   `app.pipeline.render_script_job` in a worker thread. On process restart,
   queued/running requests are re-queued from SQLite.
4. Text input uses local `espeak-ng` or macOS `say` and measured sentence
   durations. Audio input is validated with FFprobe/FFmpeg, locally transcribed
   by cached `faster-whisper`, and retains Whisper segment timestamps for SRT
   captions and transcript-scene planning.
5. FFmpeg uses the supplied audio track for audio input or synthesized speech
   for text input. It combines these with retrieved footage or generated
   fallback visuals, displays captions at their measured times, encodes each
   requested aspect ratio, and FFprobe verifies the output streams.
6. JSON transcript timeline/manifest and media files are stored locally beneath
   the configured output directory. The API returns local download routes.

## Module boundaries

- `app/main.py`: configuration, schemas, HTTP errors, routes, persistence,
  idempotency and lifecycle.
- `app/audio.py`: media validation, safe allowlisted URL downloads, local
  faster-whisper model loading, and timestamp validation.
- `app/pipeline.py`: local TTS/ASR integration, transcript timing, footage or
  fallback visual generation, captions, FFmpeg orchestration, and validation.
- `scripts/batch_runner.py`: HTTP-only batch submission and result downloader.
- `openapi.yaml`: checked-in API contract.

## Deliberate limitations

The default offline visual producer renders original topic-aware illustrations,
including separate solar-panel, solar-home, and clean-energy landscape scenes.
These generated graphics are not stock footage and do not establish commercial
B-roll suitability. The opt-in Pexels adapter is disabled until product-specific
permission is confirmed. There is no semantic embedding model, human-validated
relevance threshold, independent asset-rights verification, or human evaluation. The
adapter has a conservative title-URL keyword-overlap gate and rejects avoid
terms found in that title text; it cannot verify what a video depicts and is
not semantic matching or visual safety moderation. HTTPS audio URLs require an
exact host allowlist. Whisper model weights download on first use; audio itself
is processed locally. Segment timestamps are not word-level alignment. The
user `voice` field is recorded but does not configure the system voice; select
the engine voice through `ESPEAK_VOICE` or the host speech configuration.

SQLite and filesystem storage are suitable for this local assessment slice, not
multi-replica production. No worker lease/claim mechanism is implemented for
multiple API processes, no S3/MinIO backend exists, and callbacks/rate limiting
are absent. Do not scale this deployment horizontally until job claiming and
shared storage are designed and tested.
