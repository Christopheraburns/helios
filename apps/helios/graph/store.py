"""Content-addressed cache of materialised ontology versions.

Memgraph is a disposable copy, so the gateway keeps the normalised payload it
loaded and rebuilds from it after a pod restart. This lives on *project*
storage, unlike Memgraph's data directory: these are small write-once JSON
documents, so NFS is fine, and surviving the pod is the whole point.

This is a cache, not the system of record. O-2's publish step snapshots versions
to the lakehouse; a cache miss there just means the publisher must materialise
again.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from helios_core.ontology import OntologyGraph

_ACTIVE_POINTER = "active.json"


def store_root() -> Path:
    configured = os.environ.get("HELIOS_GRAPH_STORE_DIR")
    if configured:
        root = Path(configured)
    else:
        project = os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw")
        root = Path(project) / ".helios-graph" / "ontology"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _version_path(version: str, content_hash: str) -> Path:
    directory = store_root() / "versions" / version
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{content_hash}.json"


def _write_atomic(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, so readers never see a partial file."""
    handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def save(graph: OntologyGraph) -> Path:
    """Persist a version's payload. Idempotent: the hash names the file."""
    path = _version_path(graph.version, graph.content_hash)
    if not path.exists():
        _write_atomic(path, json.dumps(graph.to_dict(), separators=(",", ":")))
    return path


def set_active(version: str, content_hash: str) -> None:
    """Point the active marker at an already-saved version."""
    path = _version_path(version, content_hash)
    if not path.exists():
        raise FileNotFoundError(f"version {version} ({content_hash[:12]}) is not in the store")
    _write_atomic(
        store_root() / _ACTIVE_POINTER,
        json.dumps({"version": version, "content_hash": content_hash, "activated_at": time.time()}),
    )


def active_pointer() -> dict[str, Any] | None:
    try:
        return json.loads((store_root() / _ACTIVE_POINTER).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def load(version: str, content_hash: str) -> OntologyGraph:
    payload = json.loads(_version_path(version, content_hash).read_text())
    return OntologyGraph.from_dict(payload)


def load_active() -> OntologyGraph | None:
    """The version to rebuild on startup, or None when nothing is published yet."""
    pointer = active_pointer()
    if not pointer:
        return None
    try:
        return load(pointer["version"], pointer["content_hash"])
    except (OSError, KeyError):
        return None


def list_cached() -> list[dict[str, Any]]:
    """Every version in the cache, newest first."""
    pointer = active_pointer() or {}
    versions_root = store_root() / "versions"
    if not versions_root.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for directory in versions_root.iterdir():
        if not directory.is_dir():
            continue
        for payload in directory.glob("*.json"):
            content_hash = payload.stem
            entries.append(
                {
                    "version": directory.name,
                    "content_hash": content_hash,
                    "size_bytes": payload.stat().st_size,
                    "saved_at": payload.stat().st_mtime,
                    "active": pointer.get("version") == directory.name
                    and pointer.get("content_hash") == content_hash,
                }
            )
    entries.sort(key=lambda entry: entry["saved_at"], reverse=True)
    return entries
