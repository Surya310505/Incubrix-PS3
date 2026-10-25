# Official assessment requirements traceability

This version is grounded in the complete 7-page
[official PDF brief](IncuBrix_Assessment_3_Script_to_Broll_Video_Engine_Brief.pdf)
and all eight visible tabs of the original
[Submission Form workbook](IncuBrix_Assessment_3_Script_to_Broll_Video_Engine_Submission_Form.xlsx).
The original workbook has not been edited. Its sheet names, formulas, dropdown
validations and visual formatting were inspected and retained as supplied.

Evidence paths are relative to this document. Statuses describe current
implementation, not the desired final state:

- `VERIFIED`: the exact criterion has a passing test or preserved run evidence.
- `PARTIAL`: some behavior exists, but at least one required condition remains.
- `NOT_STARTED`: implementation/evidence does not exist.
- `BLOCKED`: external access, credentials, user data, hosting, or other essential
  input is required.
- `IN_PROGRESS`: work is actively being implemented; do not claim completion.

## Must-haves — PDF page 2; workbook `2_Must_haves!B6:E13`

| ID | Official requirement | Implementation and evidence | Status | Remaining work / limitation |
|---|---|---|---|---|
| M1 | Accept MP3/WAV/M4A voiceover up to 10 minutes, or text that generates narration; scene input is shown in the API contract. | Multipart upload and allowlisted HTTPS URL input are implemented in [app/main.py](../app/main.py), [app/audio.py](../app/audio.py), and [app/pipeline.py](../app/pipeline.py); audio e2e uses mocked ASR in [tests/e2e/test_jobs.py](../tests/e2e/test_jobs.py). Local Whisper, type, duration, silence, size, URL host and redirect controls have unit/integration coverage. | PARTIAL | The full local model and real speech transcription have not been run against a representative user recording; first-use model download and language/accent accuracy need live validation. |
| M2 | Segment by meaning, use sentence/phrase boundaries, cover every narration second, and let narration timing control the video. | [app/audio.py](../app/audio.py) uses faster-whisper segment timestamps; [app/pipeline.py](../app/pipeline.py) aligns SRT cues to those measured times, preserves the supplied audio track, records transcript times, and extends visual scene intervals to cover the full audio duration. `_validate_scene_timing` rejects invalid timestamps, gaps, overlaps, and incomplete coverage; [unit tests](../tests/unit/test_timeline.py) cover those cases. Mocked audio [e2e test](../tests/e2e/test_jobs.py) checks timing and output duration. | PARTIAL | Whisper chunks are not guaranteed to be semantically meaningful scene boundaries, and no representative recording evaluation or 20-second-to-5-minute evaluated coverage set exists. The hand-authored illustration categories are limited heuristics, not semantic scene understanding. |
| M3 | Every clip must have a commercially usable source, licence, attribution and link; no watermarks; identify AI-generated clips. | Optional Pexels adapter in [app/footage.py](../app/footage.py) records video page, creator, attribution, and licence reference per clip in [timeline/manifest generation](../app/pipeline.py); mocked rendering coverage is in [integration tests](../tests/integration/test_pipeline.py). The offline fallback is explicitly identified as generated graphics, not stock footage. | PARTIAL | Provider is disabled until Pexels confirms product-specific API permission. No Pexels account/live assets were used; generated illustrations are not commercially licensed B-roll and their commercial suitability has not been assessed. |
| M4 | Every requested ratio; H.264/AAC, 30 fps, 1080p-class; normalized clip streams; no black/frozen frames or audio drift. | [app/pipeline.py](../app/pipeline.py) validates final codec, dimensions, average/frame rate, `yuv420p`, audio rate, track start/duration drift, and uses FFmpeg black/freeze detection. [Integration tests](../tests/integration/test_pipeline.py) render all ratios and detect synthetic black/frozen clips. A real three-ratio run and per-output QA/FFprobe data are preserved in [M4 evidence](../evidence/acceptance/m4/README.md). | PARTIAL | Automated checks cover generated test outputs, but edge-subject crop quality and broader source-specific QA need watched review. Detector results are not a human quality score. |
| M5 | No repeated clips and no nonsense matches; meaning over keywords; score relevance 1–5 during evaluation. | Optional [Pexels adapter](../app/footage.py) searches visual hint/narration text, avoids duplicate asset IDs within a job, gates candidates using title-URL keyword overlap, and records that overlap separately from null human relevance; mocked [integration test](../tests/integration/test_pipeline.py) verifies rejection, duplicate fallback, and provenance fields. | PARTIAL | Pexels is not enabled or approved; title keywords are not semantic relevance. No live assets, human scores, or ten-video evaluation exist. |
| M6 | No unauthorized realistic faces, invented logos, or disrespectful sensitive-topic footage; handle names/brands/news safely. | Offline default uses original illustrated graphics without realistic faces or third-party footage; optional Pexels results preserve source and attribution in [app/pipeline.py](../app/pipeline.py). Avoid terms matching the candidate page URL text are rejected; candidates lacking descriptive URL keywords fall back to generated visuals. | PARTIAL | URL text does not verify video content. No named-entity handling, visual avoid enforcement, sensitive-topic detection, or safety classifier exists. |
| M7 | Run as a background job with progress; survive source failure using another provider or safe fallback. | SQLite job API and restart re-queue logic in [app/main.py](../app/main.py); optional provider errors/empty results and corrupt media use an explicit, recorded procedural fallback in [app/pipeline.py](../app/pipeline.py), covered with mocks in [unit](../tests/unit/test_footage.py) and [integration tests](../tests/integration/test_pipeline.py). | PARTIAL | No second footage source or multi-worker lease/claim; provider failure behavior has mock coverage only, not live-service or multi-user production stress. |
| M8 | Follow the Integration Standard and keep a working live demo through review. | Optional S3-compatible storage in [app/storage.py](../app/storage.py) creates the configured bucket, uploads job outputs, and streams downloads from object storage. Compose accepts external S3 endpoint settings; unit tests cover bucket creation, upload/read, missing objects, the API streaming route, and disabled-by-default filesystem behavior. Docker suite: 55 passed, 1 skipped with storage disabled. See [M8 local container evidence](../evidence/acceptance/m8/README.md). | PARTIAL | A live MinIO/S3 endpoint could not be verified: Docker Hub no longer serves `minio/minio` and Quay returned 401 in this environment. No public deployment, reviewer access, or review-period availability is established. Host port 8000 was occupied; rate limiting and complete schema-level OpenAPI conformance remain outstanding. |

## Levels — PDF page 3; workbook `3_Levels!B5:E15`

| Level item | Implementation and evidence | Status | Remaining work |
|---|---|---|---|
| Core: all must-haves pass. | Must-haves M1–M8 above. | NOT_STARTED | Every must-have must pass before Core is reached. |
| Core: ten varied inputs (content types, accents, 20 seconds–5 minutes) all produce usable videos. | Two short smoke inputs are preserved in [test pack](../evidence/test-pack/README.md); integration tests are not the ten-input evaluation set. | NOT_STARTED | Create/run the required ten varied inputs with actual narration durations, outputs, failures and logs. |
| Core: reviewer average relevance >=3.5/5 on those ten videos. | No human ratings exist. | NOT_STARTED | Human reviewers must rate scenes; do not substitute model or guessed scores. |
| Advanced: two or more footage sources and measurable selection rule recorded per scene. | One optional Pexels adapter records search result rank and the no-repeat selection rule in the timeline. | PARTIAL | Need a second independently approved/licensed source and product permission for Pexels; the selection is not semantic or human-validated. |
| Advanced: replace one scene clip and re-render only affected portion faster than a full render. | No replacement endpoint or incremental render cache. | NOT_STARTED | Implement and benchmark both paths. |
| Advanced: normalize colour/brightness and avoid flashing 0.5-second clips. | Procedural palette is controlled, but no normalization or minimum scene duration rule. | NOT_STARTED | Measure/normalize source footage and test pacing. |
| Advanced: accept IncuBrix scene lists (text and visual hint). | Request schema and renderer accept `scenes`; hint is recorded in the timeline. [app/main.py](../app/main.py), [app/pipeline.py](../app/pipeline.py); request acceptance is tested in [API tests](../tests/unit/test_api.py). | PARTIAL | Visual hints do not affect retrieval/selection because no footage retrieval exists. |
| Outstanding: independent vision-language relevance vs. human-rating agreement. | No evaluator or ratings. | NOT_STARTED | Implement only after Core and compare against human results. |
| Outstanding: Hindi and Tamil narration as well as English. | Host TTS capability only; no language test or configured Hindi/Tamil provider. | NOT_STARTED | Implement and validate both languages. |
| Outstanding: production time/cost per minute and concurrent jobs/server. | Render timing is recorded; production benchmark and cost are unknown. | NOT_STARTED | Run controlled cold/warm and concurrent benchmarks; measure cost/resources. |
| Outstanding: >=85% scenes rated 4 or 5 by reviewers. | No human scene ratings. | NOT_STARTED | Measure after Core evaluation; do not infer. |

## Integration Standard — PDF pages 3–4; workbook `4_Integration!B5:D14`

| Point | Implementation and evidence | Status | Remaining work / limitation |
|---|---|---|---|
| 1. One-command startup and test; no API keys; `.env.example` lists settings. | [compose.yaml](../compose.yaml), [Dockerfile](../Dockerfile), [pytest.ini](../pytest.ini), [.env.example](../.env.example); Compose test profile built and passed 55 tests, with one opt-in Whisper test skipped. A real API container smoke run is preserved in [M8 evidence](../evidence/acceptance/m8/README.md). | PARTIAL | Default port 8000 was already allocated; API runtime was verified on 18000. Compose defaults to SQLite/filesystem and accepts optional external S3-compatible configuration; no live object store was available. |
| 2. OpenAPI 3.1 exactly matches the running API; routes under `/api/v1/`. | [openapi.yaml](../openapi.yaml), versioned docs route in [app/main.py](../app/main.py); OpenAPI 3.1 YAML parsing and a test comparing every documented path/method with FastAPI's runtime route inventory pass. | PARTIAL | Runtime path/method parity is verified; request/response schema equivalence has not been exhaustively validated. Health and readiness aliases are also intentionally exposed at the root. |
| 3. >2-second operations are background jobs; 202, job ID, status/progress/links. Optional HMAC callback. | [app/main.py](../app/main.py); [end-to-end test](../tests/e2e/test_jobs.py) and [batch evidence](../evidence/test-pack/run-002/demo/job.json). | PARTIAL | 202/status/result links work. Callback URL and `X-IncuBrix-Signature` HMAC-SHA256 are not implemented. Progress is coarse and no multi-worker coordination exists. |
| 4. Every POST requires idempotency; same request never executes twice. | SQLite unique key and conflict/replay behavior in [app/main.py](../app/main.py); [API tests](../tests/unit/test_api.py) assert concurrent same-key submissions return one job and invoke processing once for that key. | VERIFIED | The test covers concurrent duplicate requests and a separate independent request; production-scale load remains untested. |
| 5. JSON error envelope, uppercase codes, codes listed in README. | HTTP/validation error handlers in [app/main.py](../app/main.py); [README](../README.md); unknown-route envelope is tested in [API tests](../tests/unit/test_api.py). | PARTIAL | Method-not-allowed, malformed-JSON, and internal-error behavior still lack dedicated tests. |
| 6. All settings, paths, model names and limits configurable; no hard-coded machine paths/secrets. | Environment settings in [app/main.py](../app/main.py), [app/pipeline.py](../app/pipeline.py), [.env.example](../.env.example), [compose.yaml](../compose.yaml). | PARTIAL | Audio limit is unused; renderer dimensions/rate and SQLite timeout are fixed; configurable-provider/model selection is incomplete. |
| 7. Upload/URL input; S3-compatible outputs (MinIO allowed); download links. | Multipart audio upload and allowlisted HTTPS URL processing are in [app/main.py](../app/main.py) and [app/audio.py](../app/audio.py); optional S3-compatible output storage is implemented in [app/storage.py](../app/storage.py). Unit tests cover object upload/read and the API streaming route; audio [e2e test](../tests/e2e/test_jobs.py) verifies upload-to-download. | PARTIAL | Live MinIO/S3 service integration is unverified. URL downloads require an explicit `AUDIO_URL_ALLOWED_HOSTS` configuration. Pexels API use is disabled pending product-specific permission. |
| 8. Structured JSON logs with timestamp, level, job ID and request correlation ID; health/ready. | Logging middleware and endpoints in [app/main.py](../app/main.py); health/readiness and correlation header tested in [API tests](../tests/unit/test_api.py). | PARTIAL | `job_finished`/`job_failed` worker logs omit request correlation ID; health JSON log contents not asserted. |
| 9. Manifest has model/provider versions, settings, seed, timings, cost, warnings, rights. | Manifest generation in [app/pipeline.py](../app/pipeline.py) records Pexels attribution/licence references and explicit permission/rights warnings when mocked or opted-in footage is selected; preserved offline [manifest](../evidence/test-pack/run-002/demo/manifest.json). | PARTIAL | Local cost is unknown, system TTS version is not queried, Pexels product permission is unconfirmed, and individual asset rights are not independently verified. |
| 10. CLI batch file/folder runner writes results to one directory. | [scripts/batch_runner.py](../scripts/batch_runner.py); successful live API batch run and downloaded outputs at [run 002](../evidence/test-pack/run-002/). | VERIFIED | Add automated failure/timeout/invalid-input tests; current successful folder smoke evidence covers the required behavior. |

## Hard cases and hidden screening tests — PDF page 3; workbook `6_Hard_cases!B5:E11`

The assessment says the reviewer runs five hidden tests drawn from these kinds of
cases. Do not claim the hidden-test outcome in advance.

| Case | Current behavior/evidence | Status | Required test/work |
|---|---|---|---|
| Abstract lines and numbers/statistics | Produces unrelated procedural graphics; no semantic match. | NOT_STARTED | Use meaning-aware selection; record explicit low-confidence fallback. |
| Named real person or brand | No real footage/logo is generated, but no named-entity policy. | PARTIAL | Test safe wording/warnings and prohibit fabricated likeness/logo footage. |
| Double meanings: value, bank, cloud, mouse, python | No semantic matching; offline illustrations use a small set of keyword categories. | NOT_STARTED | Add contrastive test inputs and verify chosen scene assets/reasons. |
| Sad/sensitive news | Illustrated graphics are used, but there is no topic detection or explicit warning. | PARTIAL | Add policy/test and evidence of respectful fallback. |
| 9:16 crop where subject is at edge of 16:9 source | Current visuals are created directly at target ratio, not cropped source footage. | NOT_STARTED | Add a source with edge subject and verify crop/pad framing. |
| Five-minute, silent, music-only, wrong type, empty script | Duration/container/audio-stream/silence validation exists in [app/audio.py](../app/audio.py); API rejects unsupported upload extension/MIME; audio validation tests cover supported containers, corruption and silence. No-speech audio fails transcription. | PARTIAL | Add representative five-minute and music-only recordings and run the real Whisper model; actual ASR quality has not been evaluated. |
| Provider down/empty, corrupt download, two simultaneous users | No external footage provider; SQLite unique key handles basic duplicate requests. | NOT_STARTED | Add provider fault injection, corrupt-media tests, and concurrent request stress test. |

## Metrics, submission form, and evidence deliverables

Source: PDF pages 4–7 and workbook tabs `0_Start_Here`, `1_Your_Details`,
`5_Metrics`, `7_Declaration`.

| Requirement | Evidence/status | Remaining work |
|---|---|---|
| Report cold/warm turnaround for 60 seconds of narration | Only short local smoke timings; not comparable. `NOT_STARTED`. | Benchmark 60-second voiceovers, cold and warm, with hardware/run details. |
| Cost per minute including footage, generation, TTS and compute | No external services; local compute cost unknown. `NOT_STARTED`. | Measure resource cost and include provider/API costs. |
| Coverage: narration share with clips rated >=3 | No human ratings. `NOT_STARTED`. | Calculate from human ratings/timeline durations. |
| Human relevance 1–5 per scene; independent model for Outstanding | Not measured. `NOT_STARTED`. | Collect actual reviewer ratings; no fabricated values. |
| Reliability/success rate and repeat rate | Test suite passes; no evaluation denominator or footage repeat metric. `PARTIAL`. | Run full assessment pack and calculate exact counts. |
| Peak memory/GPU time where applicable | Not measured; host used CPU/local TTS. `NOT_STARTED`. | Measure and report hardware and conditions. |
| All inputs, outputs (including failures), logs, video pack and report-claim index | [evidence/test-pack](../evidence/test-pack/README.md) currently has two successful sample inputs and documented development failures. `PARTIAL`. | Preserve every formal evaluation input/output/log, including failed and weak media results; link every report metric. |
| `README.md` <=10 setup commands, API, architecture, tests, batch, all settings | [README](../README.md). `PARTIAL`. | Add architecture diagram, error-code catalogue, exact config coverage, and reviewer-ready instructions. |
| `REPORT.md` 2–4 pages, alternatives, limitations, measured metrics, cost/unit, next steps | [REPORT](../REPORT.md). `PARTIAL`. | Add measured per-unit results only after actual benchmark; current numbers are explicitly unmeasured. |
| `SOURCES.md` and `AI_USE.md` disclosures | [SOURCES](../SOURCES.md), [AI_USE](../AI_USE.md). `PARTIAL`. | Verify exact transitive/system package licences and all final providers/assets. |
| Unedited <=8-minute demo showing clean run, failure handling, tests and a code decision | No recording. `NOT_STARTED`. | Record after the implementation stabilizes; do not edit the recording. |
| Working live demo URL/token/cold-start and rate limit; private repository with reviewer access and real history | Not available; current workspace has no Git repository or deployed service. `BLOCKED`. | User must authorize hosting/repository access, provide reviewer details and maintain service through review. |
| Submission Form: complete every claim/evidence, metrics, hard cases, details, declaration | Original workbook is preserved unchanged; personal/contact details and declaration are not filled. `BLOCKED`. | Create a separate formatted copy only after user supplies personal/demo/repository details and verified evidence. Never sign on the user's behalf. |

## Source inspection record

- The PDF is seven pages and all pages were extracted and reviewed.
- The workbook has eight visible tabs: `0_Start_Here`, `1_Your_Details`,
  `2_Must_haves`, `3_Levels`, `4_Integration`, `5_Metrics`, `6_Hard_cases`,
  and `7_Declaration`. Formulas, merged ranges, styles and dropdown validations
  were inspected. The original workbook remains unchanged.
- Workbook input hash at inspection: `dc4198db9158047086e2d3798015a2c8dbb3ce245e910afcd09a31e31e0e59b5`.
- Official PDF hash at inspection: `3dedf5f3d19c1607efddf28da3e6235808f2c25c235f882ae356da5eb7ac8c64`.
- The official brief makes all eight must-haves pass/fail. Core is required;
  levels are sequential. Integration is 20 marks, five hidden tests are 20
  marks, and the live session is 20 marks. A false claim can disqualify the
  submission, so no unmeasured ratings or blank form fields are inferred.
