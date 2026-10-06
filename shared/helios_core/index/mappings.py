"""Source-to-ontology mapping versions in helios_index (CG-6).

A mapping says which warehouse tables hold which ontology classes and which
columns identify their instances; the crawler builds its dictionary and its
resolution from it. It used to be a file in the repository. Here it is a
versioned document, like the crawler settings: saving stores the next version
number (identical content returns the existing version), versions never change,
and activations are an append-only log **per semantic model**.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime

from ..ontology.mapping import SourceMapping
from .records import MappingActivationRecord, MappingRecord
from .store import IndexStore

VERSIONS = "helios_index.mappings"
ACTIVATIONS = "helios_index.mapping_activations"


class UnknownMappingVersion(KeyError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def canonical_json(mapping: SourceMapping) -> str:
    return json.dumps(
        mapping.model_dump(mode="json", by_alias=True), sort_keys=True, separators=(",", ":")
    )


def content_hash(mapping: SourceMapping) -> str:
    return hashlib.sha256(canonical_json(mapping).encode()).hexdigest()


def mapping_of(record: MappingRecord) -> SourceMapping:
    return SourceMapping.model_validate_json(record.mapping_json)


def versions(store: IndexStore) -> list[MappingRecord]:
    records = [r for r in store.read(VERSIONS) if isinstance(r, MappingRecord)]
    return sorted(records, key=lambda r: r.version)


def save(
    store: IndexStore,
    mapping: SourceMapping,
    created_by: str,
    note: str = "",
    clock: Callable[[], str] = _now,
) -> tuple[MappingRecord, bool]:
    """Store ``mapping`` as a new version. Returns (record, created)."""
    existing = versions(store)
    digest = content_hash(mapping)
    for record in existing:
        if record.content_hash == digest:
            return record, False
    record = MappingRecord(
        version=(existing[-1].version + 1) if existing else 1,
        model=mapping.model,
        ontology_version=mapping.ontology_version,
        content_hash=digest,
        mapping_json=canonical_json(mapping),
        created_at=clock(),
        created_by=created_by,
        note=note[:2000],
    )
    store.append(VERSIONS, [record])
    return record, True


def get(store: IndexStore, version: int) -> MappingRecord:
    for record in store.read(VERSIONS, {"version": version}):
        assert isinstance(record, MappingRecord)
        return record
    raise UnknownMappingVersion(version)


def activate(
    store: IndexStore, version: int, activated_by: str, clock: Callable[[], str] = _now
) -> MappingActivationRecord:
    record = get(store, version)
    activation = MappingActivationRecord(
        version=record.version,
        model=record.model,
        content_hash=record.content_hash,
        activated_at=clock(),
        activated_by=activated_by,
    )
    store.append(ACTIVATIONS, [activation])
    return activation


def active(store: IndexStore) -> dict[str, MappingRecord]:
    """model -> its active mapping version."""
    latest: dict[str, MappingActivationRecord] = {}
    for record in store.read(ACTIVATIONS):
        if isinstance(record, MappingActivationRecord):
            current = latest.get(record.model)
            if current is None or record.activated_at >= current.activated_at:
                latest[record.model] = record
    return {model: get(store, activation.version) for model, activation in sorted(latest.items())}


def active_mappings(store: IndexStore) -> list[SourceMapping]:
    return [mapping_of(record) for record in active(store).values()]
