# M7 job resilience evidence index

The latest full suite passed API job creation/download, concurrent
idempotency, and mocked corrupt-footage fallback coverage. See:

- `tests/e2e/test_jobs.py::test_http_job_reaches_validated_downloadable_video`
- `tests/unit/test_api.py::test_concurrent_submissions_do_not_duplicate_idempotent_jobs`
- `tests/integration/test_pipeline.py::test_mock_pexels_asset_is_rendered_with_creator_and_license_metadata`

There is no evidence of durable multi-process claims, bounded job retries,
restart recovery under concurrent workers, live provider shutdown, or
production concurrency. The existing startup requeue behavior is not a
distributed worker lease.
