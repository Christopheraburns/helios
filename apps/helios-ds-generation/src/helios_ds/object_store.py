"""Object store for generated native bytes, with write-if-hash-matches semantics.

``put(key, data)`` behaves as the spec requires for idempotent workers:

- object absent -> write, then read back and verify the SHA-256
- object present with the same hash -> success, nothing written
- object present with a different hash -> ``DeterminismIntegrityError``;
  the existing object is never overwritten

Backends: ``LocalObjectStore`` (project filesystem, development and tests) and
``S3ObjectStore`` (the environment's S3 bucket, through a Cloudera AI Workbench
data connection, which authorizes each request via Ranger/RAZ). Keys under
``datasets/`` hold crawlable source artifacts; generator-internal objects such
as manifests go under ``_manifests/``, which the source connector must not
enumerate.
"""

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

MANIFEST_PREFIX = "_manifests"


class DeterminismIntegrityError(RuntimeError):
    """An object already exists at the key with different bytes."""


@dataclass(frozen=True)
class PutResult:
    key: str
    sha256: str
    size_bytes: int
    created: bool
    locator: Dict[str, Any]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_key(key: str) -> str:
    if not key or key.startswith("/") or ".." in key.split("/"):
        raise ValueError(f"invalid object key {key!r}")
    return key


class ObjectStore:
    def locator(self, key: str) -> Dict[str, Any]:
        raise NotImplementedError

    def get(self, key: str) -> Optional[bytes]:
        """Object bytes, or None if absent."""
        raise NotImplementedError

    def _write_new(self, key: str, data: bytes) -> bool:
        """Write only if absent. Return False if an object appeared concurrently."""
        raise NotImplementedError

    def put(self, key: str, data: bytes) -> PutResult:
        key = _check_key(key)
        digest = sha256_hex(data)
        existing = self.get(key)
        created = False
        if existing is None:
            created = self._write_new(key, data)
            existing = self.get(key)
        if existing is None or sha256_hex(existing) != digest:
            raise DeterminismIntegrityError(
                f"{key}: stored object hash differs from expected {digest}"
            )
        return PutResult(key, digest, len(data), created, self.locator(key))


class LocalObjectStore(ObjectStore):
    def __init__(self, root: str):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / _check_key(key)

    def locator(self, key: str) -> Dict[str, Any]:
        return {"connector_type": "helios_ds_file", "root": str(self.root), "key": key}

    def get(self, key: str) -> Optional[bytes]:
        path = self._path(key)
        return path.read_bytes() if path.exists() else None

    def _write_new(self, key: str, data: bytes) -> bool:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.chmod(tmp, 0o644)
            os.link(tmp, path)  # fails if the object exists: never overwrites
            return True
        except FileExistsError:
            return False
        finally:
            os.unlink(tmp)


DEFAULT_S3_CONNECTION = "S3 Object Store"


class S3ObjectStore(ObjectStore):
    """An S3 bucket/prefix, used through a boto3 S3 client.

    In Workbench the client comes from the project's data connection
    (``s3_client_from_connection``), so the generator never holds AWS keys.
    """

    def __init__(self, client: Any, bucket: str, prefix: str = ""):
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")

    def _object_key(self, key: str) -> str:
        key = _check_key(key)
        return f"{self.prefix}/{key}" if self.prefix else key

    def locator(self, key: str) -> Dict[str, Any]:
        object_key = self._object_key(key)
        return {
            "connector_type": "helios_ds_s3",
            "bucket": self.bucket,
            "key": object_key,
            "uri": f"s3a://{self.bucket}/{object_key}",
        }

    def get(self, key: str) -> Optional[bytes]:
        from botocore.exceptions import ClientError

        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._object_key(key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise
        body: bytes = response["Body"].read()
        return body

    def _write_new(self, key: str, data: bytes) -> bool:
        # Absence was checked just before. A concurrent writer can only be
        # writing the same deterministic bytes; put() re-reads and verifies.
        self.client.put_object(Bucket=self.bucket, Key=self._object_key(key), Body=data)
        return True


def s3_client_from_connection(name: str = DEFAULT_S3_CONNECTION) -> Any:
    """The boto3 S3 client behind a Cloudera AI Workbench data connection."""
    import cml.data_v1 as cmldata

    return cmldata.get_connection(name).get_base_connection()
