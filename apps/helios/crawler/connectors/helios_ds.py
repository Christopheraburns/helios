"""Helios-DS datasets (CR-2): artifacts listed through helios_ds.crawlable_artifacts
(READY datasets only, neutral columns), fetched by their recorded locator. The
bucket is never listed, so objects outside the view are never read. Every fetch
is checked against the recorded SHA-256 and size.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base import Connector, SourceAsset

VIEW = "helios_ds.crawlable_artifacts"


class HeliosDsConnector(Connector):
    TYPE = "helios_ds"

    def __init__(
        self,
        source_id: str,
        scope: dict[str, Any],
        cursor: Callable[[], Any],
        read_object: Callable[[dict[str, Any]], bytes | None],
    ):
        super().__init__(source_id)
        self.dataset_id = scope["dataset_id"]
        self._cursor = cursor
        self._read_object = read_object

    def list_assets(self) -> list[SourceAsset]:
        cursor = self._cursor()
        cursor.execute(
            "SELECT artifact_id, artifact_type, mime_type, source_locator, sha256, size_bytes, "
            f"semantic_timestamp FROM {VIEW} WHERE dataset_id = ? ORDER BY artifact_id",
            [self.dataset_id],
        )
        assets = []
        for artifact_id, _kind, mime, locator, sha, size, timestamp in cursor.fetchall():
            assets.append(
                SourceAsset(
                    asset_id=str(artifact_id),
                    source=self.source_id,
                    mime_type=str(mime),
                    locator=json.loads(locator) if isinstance(locator, str) else dict(locator),
                    version=str(sha),
                    size_bytes=int(size),
                    sha256=str(sha),
                    semantic_timestamp=None if timestamp is None else str(timestamp),
                )
            )
        return assets

    def _read(self, asset: SourceAsset) -> bytes | None:
        return self._read_object(asset.locator)


def object_reader(s3_client: Any = None) -> Callable[[dict[str, Any]], bytes | None]:
    """Reads by Helios-DS locator: ``helios_ds_s3`` (bucket, key) through the given
    boto3 client, or ``helios_ds_file`` (root, key) for local corpora and tests."""

    def read(locator: dict[str, Any]) -> bytes | None:
        kind = locator.get("connector_type")
        if kind == "helios_ds_s3":
            if s3_client is None:
                raise RuntimeError("no S3 client configured")
            return read_s3(s3_client, locator["bucket"], locator["key"])
        if kind == "helios_ds_file":
            path = Path(locator["root"]) / locator["key"]
            return path.read_bytes() if path.is_file() else None
        raise ValueError(f"unsupported locator connector_type {kind!r}")

    return read


def read_s3(client: Any, bucket: str, key: str) -> bytes | None:
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404"):
            return None
        raise
    return response["Body"].read()
