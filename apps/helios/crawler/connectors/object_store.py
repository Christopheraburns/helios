"""Object stores (DS-4): S3 or Ozone (S3-compatible), through a Workbench data
connection. Lists keys under a prefix, filtered by include and exclude globs,
and uses the ETag as the version token for incremental crawls.
"""

from __future__ import annotations

import fnmatch
import mimetypes
from typing import Any

from .base import Connector, SourceAsset
from .helios_ds import read_s3

# File extension -> MIME type the analyzers understand.
MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".eml": "message/rfc822",
    ".json": "application/json",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


def mime_for(key: str) -> str:
    lowered = key.lower()
    for extension, mime in MIME_BY_EXTENSION.items():
        if lowered.endswith(extension):
            return mime
    guessed, _ = mimetypes.guess_type(key)
    return guessed or "application/octet-stream"


class ObjectStoreConnector(Connector):
    TYPE = "object_store"

    def __init__(self, source_id: str, scope: dict[str, Any], client: Any):
        super().__init__(source_id)
        self.bucket = scope["bucket"]
        self.prefix = scope.get("prefix", "").lstrip("/")
        self.include = scope.get("include") or ["*"]
        self.exclude = scope.get("exclude") or []
        self._max_bytes = int(scope.get("max_bytes", 50_000_000))
        self.max_objects = int(scope.get("max_objects", 100_000))
        self._client = client

    def max_bytes(self) -> int:
        return self._max_bytes

    def _in_scope(self, key: str) -> bool:
        relative = key[len(self.prefix) :].lstrip("/") if key.startswith(self.prefix) else key
        if key.endswith("/"):
            return False  # folder placeholder
        included = any(fnmatch.fnmatch(relative, p) for p in self.include)
        return included and not any(fnmatch.fnmatch(relative, p) for p in self.exclude)

    def list_assets(self) -> list[SourceAsset]:
        assets: list[SourceAsset] = []
        token = None
        while len(assets) < self.max_objects:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": self.prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = self._client.list_objects_v2(**kwargs)
            for item in page.get("Contents", []):
                key = item["Key"]
                if not self._in_scope(key):
                    continue
                modified = item.get("LastModified")
                assets.append(
                    SourceAsset(
                        asset_id=key,
                        source=self.source_id,
                        mime_type=mime_for(key),
                        locator={"connector_type": "s3", "bucket": self.bucket, "key": key},
                        version=f"etag:{str(item.get('ETag', '')).strip(chr(34))}:{item.get('Size')}",
                        size_bytes=int(item.get("Size", 0)),
                        semantic_timestamp=modified.isoformat()
                        if hasattr(modified, "isoformat")
                        else (str(modified) if modified else None),
                    )
                )
                if len(assets) >= self.max_objects:
                    break
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
        return assets

    def _read(self, asset: SourceAsset) -> bytes | None:
        return read_s3(self._client, asset.locator["bucket"], asset.locator["key"])
