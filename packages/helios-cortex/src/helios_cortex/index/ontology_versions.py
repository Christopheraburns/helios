"""Published ontology versions in helios_index (CR-0c).

The lakehouse is the record: each (version, content_hash) is written once, and
activations are an append-only log whose latest row is the active version.
The on-disk graph cache that the Helios Graph gateway reads is rebuilt from
here (``missing_from_cache``), so losing it loses nothing.

A version string names one content: publishing different content under an
existing version is refused, so a version always means the same ontology.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

from ..ontology.graph import OntologyGraph
from .records import OntologyActivationRecord, OntologyVersionRecord
from .store import IndexStore

VERSIONS = "helios_index.ontology_versions"
ACTIVATIONS = "helios_index.ontology_activations"


class VersionConflict(ValueError):
    """The version already exists with different content."""


class UnknownVersion(KeyError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def publish(
    store: IndexStore,
    graph: OntologyGraph,
    schema_path: str,
    published_by: str,
    clock: Callable[[], str] = _now,
) -> tuple[OntologyVersionRecord, bool]:
    """Record a version. Returns (record, created); re-publishing identical content is a no-op."""
    existing = [r for r in store.read(VERSIONS, {"version": graph.version})]
    for record in existing:
        assert isinstance(record, OntologyVersionRecord)
        if record.content_hash == graph.content_hash:
            return record, False
    if existing:
        raise VersionConflict(
            f"ontology version {graph.version} is already published with different content; "
            "publish the change under a new version"
        )
    record = OntologyVersionRecord(
        version=graph.version,
        content_hash=graph.content_hash,
        schema_path=schema_path,
        published_at=clock(),
        published_by=published_by,
        node_count=len(graph.nodes),
        edge_count=len(graph.edges),
        graph_json=graph.canonical_json(),
    )
    store.append(VERSIONS, [record])
    return record, True


def versions(store: IndexStore) -> list[OntologyVersionRecord]:
    records = [r for r in store.read(VERSIONS) if isinstance(r, OntologyVersionRecord)]
    return sorted(records, key=lambda r: r.published_at)


def activate(
    store: IndexStore, version: str, activated_by: str, clock: Callable[[], str] = _now
) -> OntologyActivationRecord:
    published = [r for r in store.read(VERSIONS, {"version": version})]
    if not published:
        raise UnknownVersion(version)
    record = OntologyActivationRecord(
        version=version,
        content_hash=published[0].content_hash,  # type: ignore[attr-defined]
        activated_at=clock(),
        activated_by=activated_by,
    )
    store.append(ACTIVATIONS, [record])
    return record


def active(store: IndexStore) -> OntologyActivationRecord | None:
    records = [r for r in store.read(ACTIVATIONS) if isinstance(r, OntologyActivationRecord)]
    return max(records, key=lambda r: r.activated_at) if records else None


def graph_of(record: OntologyVersionRecord) -> OntologyGraph:
    return OntologyGraph.from_dict(json.loads(record.graph_json))


def missing_from_cache(
    store: IndexStore, cached: set[tuple[str, str]]
) -> list[OntologyVersionRecord]:
    """Published versions whose (version, content_hash) the on-disk cache lacks."""
    return [r for r in versions(store) if (r.version, r.content_hash) not in cached]
