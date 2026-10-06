"""Browsing what the crawler found, from an ontology class down to passages
(CR-9): the entities of a class, then one entity's documents and the passages
that mention it.

Reads each source's latest successful crawl. A finished run never changes, so
its tables are kept in memory; runs that are no longer the latest are dropped.
"""

from __future__ import annotations

import posixpath
from typing import Any

from . import evidence
from .records import AssetRecord, EntityRecord, MentionRecord, RelationshipRecord, SegmentRecord
from .store import IndexStore

ASSETS = "helios_index.assets"
SEGMENTS = "helios_index.segments"
MENTIONS = "helios_index.mentions"
ENTITY_LINKS = "helios_index.entity_links"
RELATIONSHIPS = "helios_index.relationships"
MAX_DOCUMENTS = 30

_tables: dict[tuple[str, str], list[Any]] = {}


def _rows(store: IndexStore, crawl_run_id: str, table: str) -> list[Any]:
    key = (crawl_run_id, table)
    if key not in _tables:
        _tables[key] = store.read(table, {"crawl_run_id": crawl_run_id})
    return _tables[key]


def _latest(store: IndexStore) -> list[Any]:
    latest = evidence.latest_runs(store)
    keep = {run.crawl_run_id for run in latest}
    for key in [k for k in _tables if k[0] not in keep]:
        del _tables[key]
    return latest


def _entity_view(entity: EntityRecord, source: str) -> dict[str, Any]:
    return {
        "entity_id": entity.entity_id,
        "class": entity.ontology_class,
        "name": entity.canonical_name,
        "keys": list(entity.external_ids),
        "source": source,
        "crawl_run_id": entity.crawl_run_id,
    }


def class_entities(
    store: IndexStore, classes: list[str], query: str = "", limit: int = 50
) -> dict[str, Any]:
    """Entities of the given ontology classes, most-documented first."""
    wanted = {c.casefold() for c in classes}
    needle = query.strip().casefold()
    found: list[dict[str, Any]] = []
    for run in _latest(store):
        documents: dict[str, set[str]] = {}
        for rel in _rows(store, run.crawl_run_id, RELATIONSHIPS):
            if isinstance(rel, RelationshipRecord) and (rel.source_kind, rel.target_kind) == ("asset", "entity"):
                documents.setdefault(rel.target_id, set()).add(rel.source_id)
        for entity in _rows(store, run.crawl_run_id, evidence.ENTITIES):
            if not isinstance(entity, EntityRecord) or entity.ontology_class.casefold() not in wanted:
                continue
            haystack = " ".join([entity.canonical_name, *entity.external_ids]).casefold()
            if needle and needle not in haystack:
                continue
            found.append(
                {**_entity_view(entity, run.source), "documents": len(documents.get(entity.entity_id, ()))}
            )
    found.sort(key=lambda e: (-e["documents"], e["name"]))
    return {"total": len(found), "entities": found[: max(1, min(limit, 500))]}


def _document_name(asset: AssetRecord) -> str:
    locator = asset.source_locator or {}
    path = str(locator.get("key") or locator.get("uri") or locator.get("path") or "")
    return posixpath.basename(path) or asset.asset_id


def entity_documents(store: IndexStore, crawl_run_id: str, entity_id: str) -> dict[str, Any] | None:
    """One entity with its documents, the passages that mention it (with the
    character ranges of each mention), related entities and claims."""
    run = next((r for r in _latest(store) if r.crawl_run_id == crawl_run_id), None)
    if run is None:
        return None
    entities = {
        e.entity_id: e for e in _rows(store, crawl_run_id, evidence.ENTITIES) if isinstance(e, EntityRecord)
    }
    entity = entities.get(entity_id)
    if entity is None:
        return None

    relation: dict[str, str] = {}  # asset_id -> About or Mentions (About wins)
    related: list[dict[str, Any]] = []
    for rel in _rows(store, crawl_run_id, RELATIONSHIPS):
        if not isinstance(rel, RelationshipRecord):
            continue
        if rel.source_kind == "asset" and rel.target_id == entity_id:
            if relation.get(rel.source_id) != "About":
                relation[rel.source_id] = rel.relationship_type
        elif rel.source_kind == "entity" and entity_id in (rel.source_id, rel.target_id):
            other = entities.get(rel.target_id if rel.source_id == entity_id else rel.source_id)
            if other is not None:
                related.append(
                    {
                        "relationship": rel.relationship_type,
                        "direction": "out" if rel.source_id == entity_id else "in",
                        "entity_id": other.entity_id,
                        "class": other.ontology_class,
                        "name": other.canonical_name,
                    }
                )

    links = {
        link.mention_id: link
        for link in store.read(ENTITY_LINKS, {"crawl_run_id": crawl_run_id, "entity_id": entity_id})
    }
    by_segment: dict[str, list[dict[str, Any]]] = {}
    for mention in _rows(store, crawl_run_id, MENTIONS):
        link = links.get(mention.mention_id) if isinstance(mention, MentionRecord) else None
        if link is None:
            continue
        by_segment.setdefault(mention.segment_id, []).append(
            {
                "surface_form": mention.surface_form,
                "start": mention.start_offset,
                "end": mention.end_offset,
                "resolved_by": link.resolved_by,
                "link_type": link.link_type,
            }
        )
    passages: dict[str, list[dict[str, Any]]] = {}
    for segment in _rows(store, crawl_run_id, SEGMENTS):
        if isinstance(segment, SegmentRecord) and segment.segment_id in by_segment:
            passages.setdefault(segment.asset_id, []).append(
                {
                    "segment_id": segment.segment_id,
                    "segment_type": segment.segment_type,
                    "ordinal": segment.ordinal,
                    "locator": segment.locator,
                    "text": segment.text,
                    "mentions": sorted(
                        by_segment[segment.segment_id], key=lambda m: (m["start"] is None, m["start"] or 0)
                    ),
                }
            )

    documents = []
    for asset in _rows(store, crawl_run_id, ASSETS):
        if not isinstance(asset, AssetRecord):
            continue
        if asset.asset_id not in relation and asset.asset_id not in passages:
            continue
        documents.append(
            {
                "asset_id": asset.asset_id,
                "name": _document_name(asset),
                "class": asset.ontology_class,
                "mime_type": asset.mime_type,
                "timestamp": asset.semantic_timestamp,
                "relationship": relation.get(asset.asset_id, "Mentions"),
                "passages": sorted(passages.get(asset.asset_id, []), key=lambda p: p["ordinal"]),
            }
        )
    documents.sort(
        key=lambda d: (d["relationship"] != "About", -len(d["passages"]), d["timestamp"] or "", d["name"])
    )
    claims = evidence.entity_claims(store, entity_id)
    return {
        "entity": _entity_view(entity, run.source),
        "documents_total": len(documents),
        "documents": documents[:MAX_DOCUMENTS],
        "related": sorted(related, key=lambda r: (r["relationship"], r["name"]))[:50],
        "claims_total": claims["claims_total"],
        "claims": claims["claims"],
    }
