"""The Helios-DS source connector (CR-2; docs/crawler-analysis.md step 1).

Lists a dataset's artifacts through ``helios_ds.crawlable_artifacts``: READY
datasets only, neutral columns, nothing that reveals which documents belong to
one story. It then fetches each artifact by its recorded locator. It never lists
the bucket, so objects outside the view (such as generation manifests) are
never read.

Every fetch is verified: the bytes must match the recorded SHA-256 and size,
or the asset is marked ``integrity_failed`` (or ``missing``) and not analyzed.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONNECTOR = "helios_ds_s3"
VIEW = "helios_ds.crawlable_artifacts"
FETCH_WORKERS = 8
FETCH_ATTEMPTS = 3  # transient storage errors (e.g. RAZ authentication) are retried
RETRY_BACKOFF_SECONDS = 0.5

# Asset type -> ontology class (InformationAsset subclasses in core.yaml).
ONTOLOGY_CLASS = {"pdf": "Document", "email": "Message", "chat": "Message"}


@dataclass(frozen=True)
class SourceAsset:
    """One asset as the connector sees it: neutral metadata, no story information."""

    asset_id: str
    source: str  # the dataset ID
    declared_type: str  # pdf, email, chat, ...
    mime_type: str
    locator: dict[str, Any]
    sha256: str
    size_bytes: int
    semantic_timestamp: str | None

    @property
    def asset_version_id(self) -> str:
        return self.sha256

    @property
    def ontology_class(self) -> str:
        return ONTOLOGY_CLASS.get(self.declared_type, "Document")


@dataclass(frozen=True)
class Fetched:
    asset: SourceAsset
    data: bytes | None
    status: str  # fetched, integrity_failed, missing, fetch_failed
    detail: str = ""


class HeliosDsConnector:
    def __init__(
        self,
        cursor: Callable[[], Any],
        read_object: Callable[[dict[str, Any]], bytes | None],
    ):
        """``cursor`` opens a DB-API cursor as the crawler; ``read_object`` reads
        bytes by locator (None if absent)."""
        self._cursor = cursor
        self._read_object = read_object
        self._sleep = time.sleep

    def list_assets(self, dataset_id: str) -> list[SourceAsset]:
        cursor = self._cursor()
        cursor.execute(
            "SELECT artifact_id, dataset_id, artifact_type, mime_type, source_locator, "
            f"sha256, size_bytes, semantic_timestamp FROM {VIEW} WHERE dataset_id = ? "
            "ORDER BY artifact_id",
            [dataset_id],
        )
        assets = []
        for row in cursor.fetchall():
            artifact_id, source, kind, mime, locator, sha, size, timestamp = row
            assets.append(
                SourceAsset(
                    asset_id=str(artifact_id),
                    source=str(source),
                    declared_type=str(kind),
                    mime_type=str(mime),
                    locator=json.loads(locator) if isinstance(locator, str) else dict(locator),
                    sha256=str(sha),
                    size_bytes=int(size),
                    semantic_timestamp=None if timestamp is None else str(timestamp),
                )
            )
        return assets

    def fetch(self, asset: SourceAsset) -> Fetched:
        data = None
        for attempt in range(FETCH_ATTEMPTS):
            try:
                data = self._read_object(asset.locator)
                break
            except Exception as exc:  # noqa: BLE001 - retried, then recorded as fetch_failed
                if attempt + 1 == FETCH_ATTEMPTS:
                    detail = f"{type(exc).__name__}: {exc} (after {FETCH_ATTEMPTS} attempts)"
                    return Fetched(asset, None, "fetch_failed", detail[:500])
                self._sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
        if data is None:
            return Fetched(asset, None, "missing", "no object at the recorded locator")
        if len(data) != asset.size_bytes:
            return Fetched(
                asset, None, "integrity_failed", f"size {len(data)} != recorded {asset.size_bytes}"
            )
        digest = hashlib.sha256(data).hexdigest()
        if digest != asset.sha256:
            return Fetched(asset, None, "integrity_failed", f"sha256 {digest[:12]}… != recorded")
        return Fetched(asset, data, "fetched")

    def fetch_all(self, assets: Iterable[SourceAsset]) -> Iterator[Fetched]:
        """Fetch in parallel (I/O-bound), yielding results in input order. The first
        asset is fetched alone, so the storage connection (e.g. RAZ authentication)
        is established before parallel requests start."""
        items = list(assets)
        if not items:
            return
        yield self.fetch(items[0])
        with ThreadPoolExecutor(FETCH_WORKERS) as pool:
            yield from pool.map(self.fetch, items[1:])


def plan_incremental(
    assets: list[SourceAsset], previous_versions: dict[str, str]
) -> tuple[list[SourceAsset], list[SourceAsset]]:
    """Split into (to_fetch, unchanged) using the last successful run's
    asset_id -> asset_version_id."""
    to_fetch, unchanged = [], []
    for asset in assets:
        if previous_versions.get(asset.asset_id) == asset.asset_version_id:
            unchanged.append(asset)
        else:
            to_fetch.append(asset)
    return to_fetch, unchanged


# --- reading objects by locator ---------------------------------------------------


def object_reader(s3_client: Any = None) -> Callable[[dict[str, Any]], bytes | None]:
    """Reads by Helios-DS locator: ``helios_ds_s3`` (bucket, key) through the given
    boto3 client, or ``helios_ds_file`` (root, key) for local corpora and tests."""

    def read(locator: dict[str, Any]) -> bytes | None:
        kind = locator.get("connector_type")
        if kind == "helios_ds_s3":
            if s3_client is None:
                raise RuntimeError("no S3 client configured")
            try:
                response = s3_client.get_object(Bucket=locator["bucket"], Key=locator["key"])
            except Exception as exc:
                code = getattr(exc, "response", {}).get("Error", {}).get("Code")
                if code in ("NoSuchKey", "404"):
                    return None
                raise
            return response["Body"].read()
        if kind == "helios_ds_file":
            path = Path(locator["root"]) / locator["key"]
            return path.read_bytes() if path.is_file() else None
        raise ValueError(f"unsupported locator connector_type {kind!r}")

    return read


def s3_client_from_connection(name: str) -> Any:
    """The boto3 S3 client behind a Cloudera AI Workbench data connection."""
    import cml.data_v1 as cmldata

    return cmldata.get_connection(name).get_base_connection()
