"""Projecting one crawl run from helios_index into Memgraph (CR-7).

The lakehouse is canonical and Memgraph is a disposable copy: the Helios API
reads a run's rows from helios_index and pushes them here table by table; the
gateway has no Impala dependency and nothing it holds is a system of record.
Loaded runs are not rebuilt on restart -- the API re-projects on demand.

Every node carries `crawl_run_id` and `key` (the index id), so several runs
coexist and `drop_run` removes exactly one. Loading is idempotent: nodes and
edges are MERGEd on stable ids, so pushing the same rows twice leaves the
counts unchanged. Edges are created with MATCH on both ends and never create
their endpoints; rows whose ends are missing are counted as `skipped`, which
is how an out-of-order load shows up.

Labels and relationship types are interpolated into the Cypher because the
protocol cannot parameterise them, so each one is checked against the
whitelists below first; every other value is a bound parameter.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

from .bolt import BoltClient

INDEX_NODE_LABELS: frozenset[str] = frozenset(
    {"CrawlRun", "Asset", "Segment", "Mention", "Entity", "Claim"}
)

# Edges the projection itself owns.
STRUCTURAL_EDGE_TYPES: frozenset[str] = frozenset(
    {
        "IN_RUN",
        "HAS_SEGMENT",
        "FOUND_IN",
        "SAME_AS",
        "POSSIBLY_SAME_AS",
        "INSTANCE_OF",
        "SUBJECT",
        "OBJECT",
        "EVIDENCE_FOR",
    }
)

# Ontology relationship classes a helios_index.relationships row may carry.
RELATIONSHIP_TYPES: frozenset[str] = frozenset(
    {
        "Mentions",
        "About",
        "ReturnOf",
        "Contains",
        "LocatedAt",
        "PartyTo",
        "HasReason",
        "ReferencesRecord",
        "EvidenceFor",
    }
)

INDEX_EDGE_TYPES: frozenset[str] = STRUCTURAL_EDGE_TYPES | RELATIONSHIP_TYPES

# helios_index tables the projection reads, in the order their references require.
TABLES: tuple[str, ...] = (
    "assets",
    "segments",
    "mentions",
    "entities",
    "entity_links",
    "relationships",
    "claims",
    "claim_evidence",
)

LINK_TYPES: dict[str, str] = {"SameAs": "SAME_AS", "PossiblySameAs": "POSSIBLY_SAME_AS"}
END_LABELS: dict[str, str] = {"entity": "Entity", "asset": "Asset"}

TEXT_LIMIT = 2_000
_BATCH = 1000


class IndexGraphError(ValueError):
    """Rows name a label, type or table the projection refuses to build Cypher for."""


def _checked_label(label: str) -> str:
    if label not in INDEX_NODE_LABELS and label != "Class":
        raise IndexGraphError(f"refusing to build Cypher for unknown label {label!r}")
    return label


def _checked_type(edge_type: str) -> str:
    if edge_type not in INDEX_EDGE_TYPES:
        raise IndexGraphError(f"refusing to build Cypher for unknown edge type {edge_type!r}")
    return edge_type


def _chunks(items: list[Any], size: int = _BATCH) -> Iterator[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _json(value: Any) -> str:
    return json.dumps(value or {}, sort_keys=True, separators=(",", ":"))


# --- run lifecycle ------------------------------------------------------------------


def ensure_indexes(client: BoltClient) -> None:
    """Index (crawl_run_id, key) per label; the loader and every read filter on them."""
    for label in sorted(INDEX_NODE_LABELS):
        client.query(f"CREATE INDEX ON :{_checked_label(label)}(crawl_run_id)")
        client.query(f"CREATE INDEX ON :{_checked_label(label)}(key)")


def _decode_state(row: dict[str, Any]) -> dict[str, Any]:
    counts = row.pop("counts_json", None)
    row["counts"] = json.loads(counts) if counts else None
    return row


def run_state(client: BoltClient, run_id: str) -> dict[str, Any] | None:
    """The CrawlRun node, or None when the run was never begun here."""
    rows = client.query(
        "MATCH (r:CrawlRun {key: $run}) "
        "RETURN r.crawl_run_id AS crawl_run_id, r.ontology_version AS ontology_version, "
        "r.complete AS complete, r.loaded_at AS loaded_at, r.counts_json AS counts_json",
        {"run": run_id},
    )
    return _decode_state(rows[0]) if rows else None


def list_runs(client: BoltClient) -> list[dict[str, Any]]:
    rows = client.query(
        "MATCH (r:CrawlRun) "
        "RETURN r.crawl_run_id AS crawl_run_id, r.ontology_version AS ontology_version, "
        "r.complete AS complete, r.loaded_at AS loaded_at, r.counts_json AS counts_json "
        "ORDER BY r.loaded_at DESC"
    )
    return [_decode_state(row) for row in rows]


def drop_run(client: BoltClient, run_id: str) -> None:
    """Remove every node of one run, and with it every edge touching them."""
    for label in sorted(INDEX_NODE_LABELS):
        client.query(
            f"MATCH (n:{_checked_label(label)} {{crawl_run_id: $run}}) DETACH DELETE n",
            {"run": run_id},
        )


def begin_run(client: BoltClient, run_id: str, ontology_version: str) -> dict[str, Any]:
    """Create the CrawlRun node, marked incomplete so a crash mid-load stays visible."""
    client.query(
        "MERGE (r:CrawlRun {key: $run}) "
        "SET r.crawl_run_id = $run, r.ontology_version = $ontology_version, "
        "r.complete = false, r.loaded_at = $now, r.counts_json = null",
        {"run": run_id, "ontology_version": ontology_version, "now": time.time()},
    )
    return {"crawl_run_id": run_id, "ontology_version": ontology_version, "complete": False}


def counts(client: BoltClient, run_id: str) -> dict[str, dict[str, int]]:
    """Node counts by label and edge counts by type for one run.

    Edges are counted from their source node, so INSTANCE_OF (whose target is an
    ontology Class node outside the run) is included.
    """
    nodes: dict[str, int] = {}
    edges: dict[str, int] = {}
    for label in sorted(INDEX_NODE_LABELS):
        rows = client.query(
            f"MATCH (n:{_checked_label(label)} {{crawl_run_id: $run}}) RETURN count(n) AS n",
            {"run": run_id},
        )
        nodes[label] = int(rows[0]["n"]) if rows else 0
        for row in client.query(
            f"MATCH (n:{_checked_label(label)} {{crawl_run_id: $run}})-[e]->() "
            f"RETURN type(e) AS type, count(e) AS n",
            {"run": run_id},
        ):
            edges[row["type"]] = edges.get(row["type"], 0) + int(row["n"])
    return {"nodes": nodes, "edges": dict(sorted(edges.items()))}


def finish_run(client: BoltClient, run_id: str) -> dict[str, Any]:
    """Mark the run complete and store its counts on the CrawlRun node."""
    totals = counts(client, run_id)
    client.query(
        "MATCH (r:CrawlRun {key: $run}) SET r.complete = true, r.counts_json = $counts_json",
        {"run": run_id, "counts_json": _json(totals)},
    )
    return {"status": "complete", "crawl_run_id": run_id, "counts": totals}


# --- loading one table ----------------------------------------------------------------


def _merge_nodes(client: BoltClient, run_id: str, label: str, rows: list[dict[str, Any]]) -> None:
    """MERGE nodes on (crawl_run_id, key) and attach them to the CrawlRun node."""
    merge = (
        f"UNWIND $rows AS row "
        f"MERGE (n:{_checked_label(label)} {{crawl_run_id: $run, key: row.key}}) "
        f"SET n += row.properties"
    )
    link = (
        f"UNWIND $rows AS row "
        f"MATCH (n:{_checked_label(label)} {{crawl_run_id: $run, key: row.key}}) "
        f"MATCH (r:CrawlRun {{key: $run}}) "
        f"MERGE (n)-[:{_checked_type('IN_RUN')}]->(r)"
    )
    for batch in _chunks(rows):
        client.query(merge, {"run": run_id, "rows": batch})
        client.query(link, {"run": run_id, "rows": [{"key": row["key"]} for row in batch]})


def _merge_edges(
    client: BoltClient,
    run_id: str,
    edge_type: str,
    from_label: str,
    to_label: str,
    rows: list[dict[str, Any]],
) -> int:
    """MERGE edges keyed by `row.key` between nodes of the run. Returns how many
    rows found both ends; the rest are the caller's `skipped`."""
    statement = (
        f"UNWIND $rows AS row "
        f"MATCH (a:{_checked_label(from_label)} {{crawl_run_id: $run, key: row.from_key}}) "
        f"MATCH (b:{_checked_label(to_label)} {{crawl_run_id: $run, key: row.to_key}}) "
        f"MERGE (a)-[e:{_checked_type(edge_type)} {{key: row.key}}]->(b) "
        f"SET e += row.properties "
        f"RETURN count(e) AS merged"
    )
    merged = 0
    for batch in _chunks(rows):
        result = client.query(statement, {"run": run_id, "rows": batch})
        merged += int(result[0]["merged"]) if result else 0
    return merged


def _merge_class_edges(
    client: BoltClient, run_id: str, ontology_version: str, rows: list[dict[str, Any]]
) -> int:
    """Entity -INSTANCE_OF-> Class of the materialised ontology version (match only)."""
    statement = (
        "UNWIND $rows AS row "
        "MATCH (a:Entity {crawl_run_id: $run, key: row.from_key}) "
        "MATCH (b:Class {version: $version, key: row.to_key}) "
        f"MERGE (a)-[e:{_checked_type('INSTANCE_OF')} {{key: row.key}}]->(b) "
        "RETURN count(e) AS merged"
    )
    merged = 0
    for batch in _chunks(rows):
        result = client.query(statement, {"run": run_id, "version": ontology_version, "rows": batch})
        merged += int(result[0]["merged"]) if result else 0
    return merged


def _edge(key: str, from_key: str, to_key: str, **properties: Any) -> dict[str, Any]:
    return {"key": key, "from_key": from_key, "to_key": to_key, "properties": properties}


def _node(key: str, **properties: Any) -> dict[str, Any]:
    return {"key": key, "properties": properties}


def _load_assets(client: BoltClient, run_id: str, rows: list[dict[str, Any]], _: str | None) -> dict[str, int]:
    nodes = [
        _node(
            r["asset_id"],
            asset_id=r["asset_id"],
            asset_version_id=r.get("asset_version_id"),
            ontology_class=r.get("ontology_class"),
            mime_type=r.get("mime_type"),
            uri=(r.get("source_locator") or {}).get("uri") or (r.get("source_locator") or {}).get("key"),
            status=r.get("status"),
            semantic_timestamp=r.get("semantic_timestamp"),
        )
        for r in rows
    ]
    _merge_nodes(client, run_id, "Asset", nodes)
    return {"merged": len(nodes), "skipped": 0}


def _load_segments(client: BoltClient, run_id: str, rows: list[dict[str, Any]], _: str | None) -> dict[str, int]:
    nodes = [
        _node(
            r["segment_id"],
            segment_id=r["segment_id"],
            asset_id=r.get("asset_id"),
            segment_type=r.get("segment_type"),
            ordinal=r.get("ordinal"),
            locator=_json(r.get("locator")),
            text=(r.get("text") or "")[:TEXT_LIMIT],
            has_structure=bool(r.get("structure")),
        )
        for r in rows
    ]
    _merge_nodes(client, run_id, "Segment", nodes)
    edges = [_edge(r["segment_id"], r["asset_id"], r["segment_id"]) for r in rows]
    merged = _merge_edges(client, run_id, "HAS_SEGMENT", "Asset", "Segment", edges)
    return {"merged": merged, "skipped": len(rows) - merged}


def _load_mentions(client: BoltClient, run_id: str, rows: list[dict[str, Any]], _: str | None) -> dict[str, int]:
    nodes = [
        _node(
            r["mention_id"],
            mention_id=r["mention_id"],
            surface_form=r.get("surface_form"),
            proposed_class=r.get("proposed_class"),
            extractor=r.get("extractor"),
            extractor_detail=r.get("extractor_detail"),
            start_offset=r.get("start_offset"),
            end_offset=r.get("end_offset"),
            locator=_json(r.get("locator")),
        )
        for r in rows
    ]
    _merge_nodes(client, run_id, "Mention", nodes)
    edges = [_edge(r["mention_id"], r["mention_id"], r["segment_id"]) for r in rows]
    merged = _merge_edges(client, run_id, "FOUND_IN", "Mention", "Segment", edges)
    return {"merged": merged, "skipped": len(rows) - merged}


def _load_entities(
    client: BoltClient, run_id: str, rows: list[dict[str, Any]], ontology_version: str | None
) -> dict[str, int]:
    nodes = [
        _node(
            r["entity_id"],
            entity_id=r["entity_id"],
            ontology_class=r.get("ontology_class"),
            canonical_name=r.get("canonical_name"),
            external_ids=list(r.get("external_ids") or []),
        )
        for r in rows
    ]
    _merge_nodes(client, run_id, "Entity", nodes)
    if ontology_version is None:
        state = run_state(client, run_id)
        ontology_version = (state or {}).get("ontology_version") or ""
    edges = [_edge(r["entity_id"], r["entity_id"], r["ontology_class"]) for r in rows]
    classed = _merge_class_edges(client, run_id, ontology_version, edges)
    return {"merged": len(nodes), "skipped": 0, "without_class": len(rows) - classed}


def _load_entity_links(client: BoltClient, run_id: str, rows: list[dict[str, Any]], _: str | None) -> dict[str, int]:
    unknown = sorted({str(r.get("link_type")) for r in rows} - set(LINK_TYPES))
    if unknown:
        raise IndexGraphError(f"unknown link_type(s) {unknown}; expected {sorted(LINK_TYPES)}")
    merged = 0
    for link_type, edge_type in LINK_TYPES.items():
        edges = [
            _edge(r["link_id"], r["mention_id"], r["entity_id"], resolved_by=r.get("resolved_by"), score=r.get("score"))
            for r in rows
            if r.get("link_type") == link_type
        ]
        if edges:
            merged += _merge_edges(client, run_id, edge_type, "Mention", "Entity", edges)
    return {"merged": merged, "skipped": len(rows) - merged}


def _load_relationships(client: BoltClient, run_id: str, rows: list[dict[str, Any]], _: str | None) -> dict[str, int]:
    unknown = sorted({str(r.get("relationship_type")) for r in rows} - RELATIONSHIP_TYPES)
    if unknown:
        raise IndexGraphError(
            f"unknown relationship_type(s) {unknown}; expected one of {sorted(RELATIONSHIP_TYPES)}"
        )
    bad_ends = sorted({str(r.get(k)) for r in rows for k in ("source_kind", "target_kind")} - set(END_LABELS))
    if bad_ends:
        raise IndexGraphError(f"unknown source/target kind(s) {bad_ends}; expected {sorted(END_LABELS)}")
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for r in rows:
        grouped.setdefault(
            (r["relationship_type"], END_LABELS[r["source_kind"]], END_LABELS[r["target_kind"]]), []
        ).append(
            _edge(
                r["relationship_id"], r["source_id"], r["target_id"],
                resolved_by=r.get("resolved_by"), confidence=r.get("confidence"),
            )
        )
    merged = 0
    for (edge_type, from_label, to_label), edges in grouped.items():
        merged += _merge_edges(client, run_id, edge_type, from_label, to_label, edges)
    return {"merged": merged, "skipped": len(rows) - merged}


def _load_claims(client: BoltClient, run_id: str, rows: list[dict[str, Any]], _: str | None) -> dict[str, int]:
    nodes = [
        _node(
            r["claim_id"],
            claim_id=r["claim_id"],
            predicate=r.get("predicate"),
            confidence=r.get("confidence"),
            extractor=r.get("extractor"),
            object_value=r.get("object_value"),
        )
        for r in rows
    ]
    _merge_nodes(client, run_id, "Claim", nodes)
    subjects = [_edge(r["claim_id"], r["claim_id"], r["subject_entity_id"]) for r in rows]
    objects = [
        _edge(r["claim_id"], r["claim_id"], r["object_entity_id"])
        for r in rows
        if r.get("object_entity_id")
    ]
    merged = _merge_edges(client, run_id, "SUBJECT", "Claim", "Entity", subjects)
    merged_objects = _merge_edges(client, run_id, "OBJECT", "Claim", "Entity", objects) if objects else 0
    return {"merged": merged, "skipped": (len(rows) - merged) + (len(objects) - merged_objects)}


def _load_claim_evidence(client: BoltClient, run_id: str, rows: list[dict[str, Any]], _: str | None) -> dict[str, int]:
    edges = [
        _edge(
            r["evidence_id"], r["segment_id"], r["claim_id"],
            locator=_json(r.get("locator")), excerpt=r.get("excerpt"),
        )
        for r in rows
    ]
    merged = _merge_edges(client, run_id, "EVIDENCE_FOR", "Segment", "Claim", edges)
    return {"merged": merged, "skipped": len(rows) - merged}


_LOADERS = {
    "assets": _load_assets,
    "segments": _load_segments,
    "mentions": _load_mentions,
    "entities": _load_entities,
    "entity_links": _load_entity_links,
    "relationships": _load_relationships,
    "claims": _load_claims,
    "claim_evidence": _load_claim_evidence,
}


def load_batch(
    client: BoltClient,
    run_id: str,
    table: str,
    rows: list[dict[str, Any]],
    ontology_version: str | None = None,
) -> dict[str, int]:
    """Load one table's rows (helios_index row dicts) for a run.

    Returns `merged` (rows fully applied) and `skipped` (edge ends that were not
    in the graph, e.g. links pushed before their entities); entities also report
    `without_class` for rows whose ontology class has no node in the materialised
    ontology version. Unknown relationship or link types raise IndexGraphError
    before anything is written.
    """
    if table not in _LOADERS:
        raise IndexGraphError(f"unknown index table {table!r}; expected one of {list(TABLES)}")
    if not rows:
        return {"merged": 0, "skipped": 0}
    return _LOADERS[table](client, run_id, rows, ontology_version)
