# M2 scene timing evidence index

The pipeline now validates contiguous, finite scene intervals and complete
narration coverage. Unit cases are in
`tests/unit/test_timeline.py`; integration/E2E timing checks are in
`tests/integration/test_pipeline.py` and `tests/e2e/test_jobs.py`.

[scene-timing-review.csv](scene-timing-review.csv) is derived from the real
three-ratio render under `../m4/`. Its human-review column explicitly records
that no reviewer watch/meaning score has been completed. The timestamps prove
the renderer's interval layout, not semantic boundary quality.
