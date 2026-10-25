# M4 output-quality evidence

This run verifies the offline renderer on all requested output ratios. The
video files, timeline, manifest, and machine-generated `video-qa.json` are
from an actual local pipeline run, not mocked media.

Reproduce from the project root with the local dependencies and FFmpeg/FFprobe
installed:

```bash
python -c 'import json, uuid; from pathlib import Path; from app.pipeline import render_script_job; request=json.loads(Path("evidence/acceptance/m4/request.json").read_text()); render_script_job("m4-local-evidence", request, Path("evidence/acceptance/m4"))'
```

The QA report records codec, dimensions, frame rate, pixel format, time base,
audio codec/sample rate, track start and duration drift, and detected black or
frozen intervals. The current detector uses FFmpeg `blackdetect` and
`freezedetect`; it is a machine check, not a substitute for watching the videos
or evaluating crop quality.
