from __future__ import annotations

import io
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from app import storage


class FakeS3Client:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.created_buckets: list[str] = []
        self.bucket_configurations: list[dict] = []

    def head_bucket(self, *, Bucket: str):
        if Bucket not in self.created_buckets:
            raise ClientError({"Error": {"Code": "404"}}, "HeadBucket")

    def create_bucket(self, *, Bucket: str, **kwargs):
        self.created_buckets.append(Bucket)
        self.bucket_configurations.append(kwargs)

    def upload_file(self, filename: str, bucket: str, key: str):
        self.objects[f"{bucket}/{key}"] = Path(filename).read_bytes()

    def get_object(self, *, Bucket: str, Key: str):
        try:
            body = self.objects[f"{Bucket}/{Key}"]
        except KeyError as exc:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject") from exc
        return {"Body": io.BytesIO(body), "ContentLength": len(body)}


def configure_storage(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OBJECT_STORE_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("OBJECT_STORE_ACCESS_KEY_ID", "local-user")
    monkeypatch.setenv("OBJECT_STORE_SECRET_ACCESS_KEY", "local-password")
    monkeypatch.setenv("OBJECT_STORE_BUCKET", "test-outputs")
    client = FakeS3Client()
    monkeypatch.setattr(storage.boto3, "client", lambda *args, **kwargs: client)
    return client


def test_object_storage_is_disabled_without_endpoint(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OBJECT_STORE_ENDPOINT_URL", raising=False)
    assert storage.create_object_storage() is None


def test_object_storage_requires_credentials(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OBJECT_STORE_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.delenv("OBJECT_STORE_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("OBJECT_STORE_SECRET_ACCESS_KEY", raising=False)

    with pytest.raises(ValueError, match="ACCESS_KEY_ID"):
        storage.create_object_storage()


def test_upload_and_stream_object_and_create_missing_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    client = configure_storage(monkeypatch)
    adapter = storage.create_object_storage()
    assert adapter is not None
    adapter.ensure_bucket()
    assert client.created_buckets == ["test-outputs"]

    output = tmp_path / "video-qa.json"
    output.write_text('{"status":"passed"}', encoding="utf-8")
    adapter.upload_job_outputs("job-123", tmp_path, [output.name])
    chunks, length = adapter.read_object("job-123", output.name)

    assert b"".join(chunks) == output.read_bytes()
    assert length == output.stat().st_size


def test_bucket_creation_uses_non_default_region(
    monkeypatch: pytest.MonkeyPatch,
):
    client = configure_storage(monkeypatch)
    monkeypatch.setenv("OBJECT_STORE_REGION", "eu-west-1")
    adapter = storage.create_object_storage()
    assert adapter is not None

    adapter.ensure_bucket()

    assert client.bucket_configurations == [
        {"CreateBucketConfiguration": {"LocationConstraint": "eu-west-1"}}
    ]


def test_missing_object_raises_specific_not_found_error(monkeypatch: pytest.MonkeyPatch):
    configure_storage(monkeypatch)
    adapter = storage.create_object_storage()
    assert adapter is not None

    with pytest.raises(storage.ObjectNotFoundError, match="missing.json"):
        adapter.read_object("job-123", "missing.json")
