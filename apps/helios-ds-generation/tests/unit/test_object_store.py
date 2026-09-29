import io

import pytest

from helios_ds.object_store import (
    DeterminismIntegrityError,
    LocalObjectStore,
    S3ObjectStore,
    sha256_hex,
)


def test_local_put_is_idempotent(tmp_path):
    store = LocalObjectStore(str(tmp_path))
    first = store.put("datasets/d/a.pdf", b"bytes")
    second = store.put("datasets/d/a.pdf", b"bytes")
    assert first.created and not second.created
    assert first.sha256 == second.sha256 == sha256_hex(b"bytes")
    assert store.get("datasets/d/a.pdf") == b"bytes"
    assert first.locator == {
        "connector_type": "helios_ds_file",
        "root": str(tmp_path),
        "key": "datasets/d/a.pdf",
    }


def test_local_mismatch_raises_and_never_overwrites(tmp_path):
    store = LocalObjectStore(str(tmp_path))
    store.put("datasets/d/a.pdf", b"original")
    with pytest.raises(DeterminismIntegrityError):
        store.put("datasets/d/a.pdf", b"different")
    assert store.get("datasets/d/a.pdf") == b"original"


def test_local_leaves_no_temp_files(tmp_path):
    store = LocalObjectStore(str(tmp_path))
    store.put("a/b.txt", b"x")
    assert sorted(p.name for p in (tmp_path / "a").iterdir()) == ["b.txt"]


@pytest.mark.parametrize("key", ["", "/abs", "a/../b", ".."])
def test_invalid_keys_are_rejected(tmp_path, key):
    with pytest.raises(ValueError):
        LocalObjectStore(str(tmp_path)).put(key, b"x")


class _FakeS3:
    """Just enough of the boto3 S3 client that a Workbench data connection returns."""

    def __init__(self, deny=False):
        self.objects = {}
        self.deny = deny

    def get_object(self, Bucket, Key):
        from botocore.exceptions import ClientError

        if self.deny:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def put_object(self, Bucket, Key, Body):
        self.objects[(Bucket, Key)] = Body


BUCKET = "applied-ai-buk-d5eff1ab"


def test_s3_put_uses_prefix_and_verifies():
    client = _FakeS3()
    store = S3ObjectStore(client, BUCKET, "helios-db/source/")
    result = store.put("datasets/d/a.eml", b"mail")
    assert result.created
    assert result.locator == {
        "connector_type": "helios_ds_s3",
        "bucket": BUCKET,
        "key": "helios-db/source/datasets/d/a.eml",
        "uri": f"s3a://{BUCKET}/helios-db/source/datasets/d/a.eml",
    }
    assert not store.put("datasets/d/a.eml", b"mail").created
    with pytest.raises(DeterminismIntegrityError):
        store.put("datasets/d/a.eml", b"other")
    assert client.objects[(BUCKET, "helios-db/source/datasets/d/a.eml")] == b"mail"


def test_s3_access_errors_are_not_mistaken_for_missing_objects():
    from botocore.exceptions import ClientError

    with pytest.raises(ClientError, match="AccessDenied"):
        S3ObjectStore(_FakeS3(deny=True), BUCKET, "p").get("datasets/x")


def test_s3a_uri_uses_the_workbench_data_connection(monkeypatch):
    from helios_ds import backends

    seen = {}

    def fake_connection(name):
        seen["name"] = name
        return _FakeS3()

    monkeypatch.setattr(backends, "s3_client_from_connection", fake_connection)
    monkeypatch.delenv("HELIOS_DS_S3_CONNECTION", raising=False)
    store = backends.object_store_from_uri(f"s3a://{BUCKET}/helios-db/source")
    assert (store.bucket, store.prefix, seen["name"]) == (
        BUCKET,
        "helios-db/source",
        "S3 Object Store",
    )

    monkeypatch.setenv("HELIOS_DS_S3_CONNECTION", "Other Connection")
    backends.object_store_from_uri(f"s3://{BUCKET}/x")
    assert seen["name"] == "Other Connection"


def test_unknown_object_store_uri_is_rejected():
    from helios_ds.backends import object_store_from_uri

    with pytest.raises(ValueError, match="s3a://"):
        object_store_from_uri("ozone://bucket/prefix")
