from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError


class ObjectNotFoundError(FileNotFoundError):
    pass


class S3ObjectStorage:
    def __init__(self) -> None:
        endpoint_url = os.getenv("OBJECT_STORE_ENDPOINT_URL", "").strip()
        access_key = os.getenv("OBJECT_STORE_ACCESS_KEY_ID", "").strip()
        secret_key = os.getenv("OBJECT_STORE_SECRET_ACCESS_KEY", "").strip()
        if not endpoint_url:
            raise ValueError("OBJECT_STORE_ENDPOINT_URL is required for object storage.")
        if not access_key or not secret_key:
            raise ValueError(
                "OBJECT_STORE_ACCESS_KEY_ID and OBJECT_STORE_SECRET_ACCESS_KEY "
                "are required for object storage."
            )
        self.bucket = os.getenv("OBJECT_STORE_BUCKET", "incubrix-outputs").strip()
        if not self.bucket:
            raise ValueError("OBJECT_STORE_BUCKET must not be empty.")
        self.region = os.getenv("OBJECT_STORE_REGION", "us-east-1").strip() or "us-east-1"
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=self.region,
        )

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code not in {"404", "NoSuchBucket", "NotFound"}:
                raise
            create_options = {}
            if self.region != "us-east-1":
                create_options["CreateBucketConfiguration"] = {
                    "LocationConstraint": self.region,
                }
            self.client.create_bucket(Bucket=self.bucket, **create_options)

    def upload_job_outputs(self, job_id: str, output_dir: Path, filenames: list[str]) -> None:
        for filename in filenames:
            path = output_dir / filename
            if not path.is_file():
                raise FileNotFoundError(f"Job output is missing: {filename}")
            self.client.upload_file(
                str(path),
                self.bucket,
                f"{job_id}/{filename}",
            )

    def read_object(self, job_id: str, filename: str) -> tuple[Iterator[bytes], int]:
        try:
            response: dict[str, Any] = self.client.get_object(
                Bucket=self.bucket,
                Key=f"{job_id}/{filename}",
            )
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise ObjectNotFoundError(filename) from exc
            raise

        body = response["Body"]

        def chunks() -> Iterator[bytes]:
            try:
                while chunk := body.read(1024 * 1024):
                    yield chunk
            finally:
                body.close()

        return chunks(), int(response["ContentLength"])


def create_object_storage() -> S3ObjectStorage | None:
    if not os.getenv("OBJECT_STORE_ENDPOINT_URL", "").strip():
        return None
    return S3ObjectStorage()
