# M5 relevance evidence status

The current Pexels adapter uses a title-URL keyword gate, not semantic
matching. The timeline keeps human `relevance_score` null. No ten-input
evaluation or human scene ratings have been performed. The CSV is an empty
review template; it contains no invented scores.

Deterministic keyword-gate and duplicate/fallback tests are in
`tests/unit/test_footage.py` and
`tests/integration/test_pipeline.py`.
