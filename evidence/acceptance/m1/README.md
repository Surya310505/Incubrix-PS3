# M1 input evidence index

The current automated suite covers generated text narration and synthetic
MP3/WAV/M4A validation. The audio upload E2E substitutes transcription segments,
so it does not prove recognition accuracy. The opt-in real Whisper test was
skipped in the latest suite; no representative user recording or human-reviewed
transcript is included. See [input matrix](input-matrix.csv) and the linked
tests in `tests/integration/` and `tests/e2e/`.
