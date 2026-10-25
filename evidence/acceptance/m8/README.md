# M8 local container smoke evidence

This evidence records a real API job served from the Compose-built container,
including health/readiness, job completion, and downloads. The default Compose
host port 8000 was occupied by another process, so the container was exercised
on host port 18000 using:

```bash
docker compose run -d --name incubrix-ps3-audit-api -p 18000:8000 api
```

`docker-compose-test.log` contains the full Compose test-profile output
(55 passed, 1 skipped) with the optional S3 endpoint unset. A live MinIO/S3
service could not be started in this environment: Docker Hub no longer serves
`minio/minio`, and Quay returned 401. The API route and adapter are covered by
unit tests, and the end-to-end suite includes an assertion that removes a
local QA artifact and retrieves it from S3 when the endpoint is configured;
that end-to-end path has not been exercised against a live object store.
`run-004/` contains the latest-image request, job response, output files, and a
JSON record created by the smoke-test client. The manifest reports
`video_quality.status=passed` and links the QA summary. `run-002/` and
`run-003/` and `run-001/` retain earlier successful runs. This is local
container evidence only; it is not a hosted reviewer demo or uptime evidence.
