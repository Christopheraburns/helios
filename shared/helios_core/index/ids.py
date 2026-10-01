"""Stable IDs for helios_index rows.

IDs are derived from content, not from the crawl run, so the same asset, mention
or entity has the same ID in every run: runs can be compared row by row, and an
unchanged asset's rows can be carried forward. Rows are keyed by
(crawl_run_id, id).
"""

from __future__ import annotations

import hashlib
import uuid

_NAMESPACE = uuid.UUID("5d2b7f0e-4c41-5b8e-9a3e-6f1c2b9d7a10")  # helios_index


def _id(kind: str, *parts: object) -> str:
    digest = hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()
    return str(uuid.uuid5(_NAMESPACE, f"{kind}:{digest}"))


def entity_id(ontology_class: str, external_id: str) -> str:
    """One ID per real-world entity: the class plus its primary source key."""
    return _id("entity", ontology_class, external_id)


def segment_id(asset_version_id: str, ordinal: int) -> str:
    return _id("segment", asset_version_id, ordinal)


def mention_id(
    segment_id: str, start: int | None, end: int | None, surface: str, extractor: str
) -> str:
    return _id("mention", segment_id, start, end, surface, extractor)


def link_id(mention_id: str, entity_id: str) -> str:
    return _id("link", mention_id, entity_id)


def relationship_id(relationship_type: str, source_id: str, target_id: str) -> str:
    return _id("relationship", relationship_type, source_id, target_id)


def claim_id(predicate: str, subject_entity_id: str, object_key: str | None) -> str:
    return _id("claim", predicate, subject_entity_id, object_key)


def evidence_id(claim_id: str, segment_id: str, start: int | None, end: int | None) -> str:
    return _id("evidence", claim_id, segment_id, start, end)


def external_id(source: str, obj: str, key: dict[str, object]) -> str:
    """ "<source>.<object>:<key>" with the key's columns in name order,
    e.g. "tpcds.customer:c_customer_sk=12345"."""
    rendered = ",".join(f"{name}={key[name]}" for name in sorted(key))
    return f"{source}.{obj}:{rendered}"
