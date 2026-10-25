from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import httpx


def _load_inputs(source: Path) -> list[tuple[str, Path, dict]]:
    files = [source] if source.is_file() else sorted(source.glob("*.json"))
    inputs = []
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Input must be a JSON object: {path}")
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem).strip("-.") or "input"
        inputs.append((name, path, payload))
    if not inputs:
        raise ValueError(f"No JSON test inputs found at {source}")
    return inputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit JSON inputs and download each job's output files.")
    parser.add_argument("input", type=Path, help="A request JSON file or folder of request JSON files.")
    parser.add_argument("output", type=Path, help="Directory for results and downloaded artifacts.")
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL.")
    parser.add_argument("--timeout", type=int, default=1800, help="Maximum seconds to wait for each job.")
    args = parser.parse_args()

    failures = 0
    args.output.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=args.api.rstrip("/"), timeout=30) as client:
        for name, source_path, payload in _load_inputs(args.input):
            destination = args.output / name
            destination.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(
                source_path.read_bytes() + source_path.resolve().as_posix().encode()
            ).hexdigest()
            try:
                response = client.post(
                    "/api/v1/broll/jobs",
                    headers={"Idempotency-Key": f"batch-{digest}"},
                    json=payload,
                )
                response.raise_for_status()
                accepted = response.json()
                deadline = time.monotonic() + args.timeout
                while True:
                    status_response = client.get(accepted["status_url"])
                    status_response.raise_for_status()
                    job = status_response.json()
                    if job["status"] in {"succeeded", "failed"}:
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Job {accepted['job_id']} did not finish within {args.timeout}s")
                    time.sleep(1)
                (destination / "job.json").write_text(json.dumps(job, indent=2), encoding="utf-8")
                if job["status"] == "failed":
                    failures += 1
                    continue
                for filename, url in job["result"]["files"].items():
                    file_response = client.get(url)
                    file_response.raise_for_status()
                    (destination / filename).write_bytes(file_response.content)
                print(f"{name}: {job['status']} ({accepted['job_id']})")
            except (httpx.HTTPError, ValueError, KeyError, TimeoutError) as exc:
                failures += 1
                (destination / "failure.json").write_text(
                    json.dumps({"input": str(source_path), "error": str(exc)}, indent=2),
                    encoding="utf-8",
                )
                print(f"{name}: failed: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
