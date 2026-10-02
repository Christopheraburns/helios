"""What every connector shares (DS-1): the asset model, retried and verified
fetching, and parallel fetch with a warm-up request.

A connector knows one kind of location. It must:
- ``test()``: say whether the location is reachable with the configured credentials;
- ``list_assets()``: enumerate what is in scope, each asset with a stable native
  ID and a version token (content hash, ETag, ...) for incremental crawls;
- ``_read(asset)``: return the asset's bytes, or None if it has gone.

Everything after fetching (analysis, segments, resolution) is location-independent.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

FETCH_WORKERS = 8
FETCH_ATTEMPTS = 3  # transient storage errors (e.g. RAZ authentication) are retried
RETRY_BACKOFF_SECONDS = 0.5

# MIME type -> ontology class (InformationAsset subclasses in core.yaml).
ONTOLOGY_CLASS = {
    "application/pdf": "Document",
    "message/rfc822": "Message",
    "application/json": "Message",
    "text/plain": "Document",
    "text/markdown": "Document",
    "application/x-helios-row+json": "Document",
}


@dataclass(frozen=True)
class SourceAsset:
    """One asset as a connector sees it: neutral metadata only."""

    asset_id: str  # stable native ID within the data source
    source: str  # the data source (or legacy dataset) ID
    mime_type: str
    locator: dict[str, Any]  # how the connector reads it again
    version: str  # changes when the content changes
    size_bytes: int | None = None
    sha256: str | None = None  # verified on fetch when known
    semantic_timestamp: str | None = None

    @property
    def asset_version_id(self) -> str:
        return self.version

    @property
    def ontology_class(self) -> str:
        return ONTOLOGY_CLASS.get(self.mime_type, "Document")


@dataclass(frozen=True)
class Fetched:
    asset: SourceAsset
    data: bytes | None
    status: str  # fetched, integrity_failed, missing, fetch_failed, too_large
    detail: str = ""


@dataclass(frozen=True)
class ConnectionTest:
    ok: bool
    detail: str
    sample: list[SourceAsset]


class Connector:
    TYPE = "base"

    def __init__(self, source_id: str):
        self.source_id = source_id
        self._sleep = time.sleep

    # --- to implement ------------------------------------------------------------------

    def list_assets(self) -> list[SourceAsset]:
        raise NotImplementedError

    def _read(self, asset: SourceAsset) -> bytes | None:
        raise NotImplementedError

    def max_bytes(self) -> int | None:
        return None

    # --- shared --------------------------------------------------------------------------

    def test(self, sample_size: int = 10) -> ConnectionTest:
        """List the scope (and read one asset) to prove the location is reachable."""
        try:
            assets = self.list_assets()
        except Exception as exc:  # noqa: BLE001 - reported to the user, not raised
            return ConnectionTest(False, f"listing failed: {type(exc).__name__}: {exc}"[:500], [])
        if not assets:
            return ConnectionTest(True, "reachable, but nothing is in scope", [])
        limit = self.max_bytes()
        readable = [
            a for a in assets if limit is None or a.size_bytes is None or a.size_bytes <= limit
        ]
        if not readable:
            return ConnectionTest(
                False, f"listed {len(assets)}, all over the size limit", assets[:sample_size]
            )
        first = self.fetch(readable[0])
        if first.status != "fetched":
            return ConnectionTest(
                False,
                f"listed {len(assets)}, but reading failed: {first.detail}",
                assets[:sample_size],
            )
        return ConnectionTest(
            True, f"{len(assets)} assets in scope; reading works", assets[:sample_size]
        )

    def fetch(self, asset: SourceAsset) -> Fetched:
        limit = self.max_bytes()
        if limit is not None and asset.size_bytes is not None and asset.size_bytes > limit:
            return Fetched(asset, None, "too_large", f"{asset.size_bytes} bytes > {limit}")
        data = None
        for attempt in range(FETCH_ATTEMPTS):
            try:
                data = self._read(asset)
                break
            except Exception as exc:  # noqa: BLE001 - retried, then recorded as fetch_failed
                if attempt + 1 == FETCH_ATTEMPTS:
                    detail = f"{type(exc).__name__}: {exc} (after {FETCH_ATTEMPTS} attempts)"
                    return Fetched(asset, None, "fetch_failed", detail[:500])
                self._sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
        if data is None:
            return Fetched(asset, None, "missing", "no object at the recorded location")
        if asset.size_bytes is not None and len(data) != asset.size_bytes:
            return Fetched(
                asset, None, "integrity_failed", f"size {len(data)} != recorded {asset.size_bytes}"
            )
        if asset.sha256 is not None:
            digest = hashlib.sha256(data).hexdigest()
            if digest != asset.sha256:
                return Fetched(
                    asset, None, "integrity_failed", f"sha256 {digest[:12]}… != recorded"
                )
        return Fetched(asset, data, "fetched")

    def fetch_all(self, assets: Iterable[SourceAsset]) -> Iterator[Fetched]:
        """Fetch in parallel (I/O-bound), in input order. The first asset is fetched
        alone so the storage connection (e.g. RAZ authentication) is established first."""
        items = list(assets)
        if not items:
            return
        yield self.fetch(items[0])
        with ThreadPoolExecutor(FETCH_WORKERS) as pool:
            yield from pool.map(self.fetch, items[1:])


def plan_incremental(
    assets: list[SourceAsset], previous_versions: dict[str, str]
) -> tuple[list[SourceAsset], list[SourceAsset]]:
    """Split into (to_fetch, unchanged) using the last reusable run's
    asset_id -> asset_version_id."""
    to_fetch, unchanged = [], []
    for asset in assets:
        if previous_versions.get(asset.asset_id) == asset.asset_version_id:
            unchanged.append(asset)
        else:
            to_fetch.append(asset)
    return to_fetch, unchanged
