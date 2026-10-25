# Test pack index

This pack contains reproducible local artifacts from the script-to-video path.
The generated illustrations are evidence of the offline renderer only, not
commercial B-roll, and are not part of the ten human-rated evaluation inputs.

## Run 001

- Input: [request.json](run-001/request.json)
- Generated job result: [job.json](run-001/job.json)
- Captions: [captions.srt](run-001/artifacts/captions.srt)
- Timeline: [timeline.json](run-001/artifacts/timeline.json)
- Manifest: [manifest.json](run-001/artifacts/manifest.json)
- 16:9 output: [video_16_9.mp4](run-001/artifacts/video_16_9.mp4)
- Command/result log: [run.log](run-001/run.log)
- Earlier relative-path failure log: [failed-relative-output.log](run-001/failed-relative-output.log)

## Run 002

This run exercised `scripts/batch_runner.py` against the local HTTP API using
the public demo input. The runner retrieved every result artifact.

- Input: [demo.json](../../examples/inputs/demo.json)
- Job response: [job.json](run-002/demo/job.json)
- 16:9 output: [video_16_9.mp4](run-002/demo/video_16_9.mp4)
- Captions: [captions.srt](run-002/demo/captions.srt)
- Timeline: [timeline.json](run-002/demo/timeline.json)
- Manifest: [manifest.json](run-002/demo/manifest.json)
- Batch output: [run.log](run-002/run.log)

## Run 003

This longer solar-energy demo exercises nine narration-bound scenes using the
default offline renderer. Its original illustrations include solar panels, a
solar-powered home, and a clean-energy landscape; they are not stock footage
and do not establish commercial B-roll suitability. It was rendered directly
through the local pipeline, not submitted as a persisted API job.

- Input: [request.json](run-003/request.json)
- Render result: [job.json](run-003/demo/job.json)
- 16:9 output: [video_16_9.mp4](run-003/demo/video_16_9.mp4)
- Captions: [captions.srt](run-003/demo/captions.srt)
- Timeline: [timeline.json](run-003/demo/timeline.json)
- Manifest: [manifest.json](run-003/demo/manifest.json)

No human ratings or external asset licence claims are included. See
[development failures](DEVELOPMENT-FAILURES.md) for failed checks encountered
and corrected while building this path.
