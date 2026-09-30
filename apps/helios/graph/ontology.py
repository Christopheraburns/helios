"""Loading and querying ontology versions in Memgraph.

Every node carries `version` and `key`, so several versions coexist in one
database and a new one can be materialised while the old one still serves --
the blue/green swap is a pointer change, not a reload.

Labels and relationship types are interpolated into the Cypher because the
protocol has no way to parameterise them. That is safe only because each one is
checked against the whitelists in `helios_core.ontology` first; every other
value is a bound parameter.
"""

from __future__ import annotations

import time
from typing import Any

from helios_core.ontology import EDGE_TYPES, NODE_LABELS, OntologyGraph, OntologyGraphError

from .bolt import BoltClient

# The gateway owns this edge; the parser never emits it.
IN_VERSION = "IN_VERSION"

_MAX_DEPTH = 6
_BATCH = 1000


def _checked_label(label: str) -> str:
    if label not in NODE_LABELS:
        raise OntologyGraphError(f"refusing to build Cypher for unknown label {label!r}")
    return label


def _checked_type(edge_type: str) -> str:
    if edge_type not in EDGE_TYPES | {IN_VERSION}:
        raise OntologyGraphError(f"refusing to build Cypher for unknown edge type {edge_type!r}")
    return edge_type


def _chunks(items: list[Any], size: int = _BATCH):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def ensure_indexes(client: BoltClient) -> None:
    """Index (version, key) per label; the loader and every read filter on them."""
    for label in sorted(NODE_LABELS):
        client.query(f"CREATE INDEX ON :{_checked_label(label)}(version)")
        client.query(f"CREATE INDEX ON :{_checked_label(label)}(key)")


def version_state(client: BoltClient, version: str) -> dict[str, Any] | None:
    rows = client.query(
        "MATCH (v:OntologyVersion {key: $version}) "
        "RETURN v.version AS version, v.content_hash AS content_hash, "
        "v.complete AS complete, v.materialised_at AS materialised_at",
        {"version": version},
    )
    return rows[0] if rows else None


def drop_version(client: BoltClient, version: str) -> None:
    """Remove every node of a version, and with it every edge."""
    for label in sorted(NODE_LABELS):
        client.query(
            f"MATCH (n:{_checked_label(label)} {{version: $version}}) DETACH DELETE n",
            {"version": version},
        )


def materialise(client: BoltClient, graph: OntologyGraph, force: bool = False) -> dict[str, Any]:
    """Load a version into Memgraph. Idempotent on (version, content_hash).

    Re-posting an already-loaded version is a no-op, so the publisher can retry
    without wondering whether the graph is half-written. A version whose hash
    differs is a republish: the old subgraph is dropped first.
    """
    existing = version_state(client, graph.version)
    if (
        existing
        and existing.get("complete")
        and existing.get("content_hash") == graph.content_hash
        and not force
    ):
        return {
            "status": "unchanged",
            "version": graph.version,
            "content_hash": graph.content_hash,
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
        }

    replaced = existing is not None
    if replaced:
        drop_version(client, graph.version)

    started = time.monotonic()
    # The version node first, marked incomplete: a crash mid-load stays visible.
    client.query(
        "MERGE (v:OntologyVersion {key: $version}) "
        "SET v.version = $version, v.content_hash = $content_hash, "
        "v.complete = false, v.materialised_at = $now",
        {"version": graph.version, "content_hash": graph.content_hash, "now": time.time()},
    )

    for label, nodes in graph.nodes_by_label().items():
        if label == "OntologyVersion":
            continue  # The gateway owns the version node.
        statement = (
            f"UNWIND $rows AS row "
            f"MERGE (n:{_checked_label(label)} {{version: $version, key: row.key}}) "
            f"SET n += row.properties"
        )
        for batch in _chunks([{"key": n.key, "properties": n.properties} for n in nodes]):
            client.query(statement, {"version": graph.version, "rows": batch})

        # Membership edge, so traversals can reach the version node directly.
        link = (
            f"UNWIND $rows AS row "
            f"MATCH (n:{_checked_label(label)} {{version: $version, key: row.key}}) "
            f"MATCH (v:OntologyVersion {{key: $version}}) "
            f"MERGE (n)-[:{_checked_type(IN_VERSION)}]->(v)"
        )
        for batch in _chunks([{"key": n.key} for n in nodes]):
            client.query(link, {"version": graph.version, "rows": batch})

    # Group edges by (type, from_label, to_label): all three are interpolated.
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for edge in graph.edges:
        grouped.setdefault((edge.type, edge.from_label, edge.to_label), []).append(
            {"from_key": edge.from_key, "to_key": edge.to_key, "properties": edge.properties}
        )
    for (edge_type, from_label, to_label), rows in grouped.items():
        statement = (
            f"UNWIND $rows AS row "
            f"MATCH (a:{_checked_label(from_label)} {{version: $version, key: row.from_key}}) "
            f"MATCH (b:{_checked_label(to_label)} {{version: $version, key: row.to_key}}) "
            f"MERGE (a)-[r:{_checked_type(edge_type)}]->(b) "
            f"SET r += row.properties"
        )
        for batch in _chunks(rows):
            client.query(statement, {"version": graph.version, "rows": batch})

    client.query(
        "MATCH (v:OntologyVersion {key: $version}) SET v.complete = true",
        {"version": graph.version},
    )
    return {
        "status": "replaced" if replaced else "created",
        "version": graph.version,
        "content_hash": graph.content_hash,
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "seconds": round(time.monotonic() - started, 2),
    }


def list_versions(client: BoltClient) -> list[dict[str, Any]]:
    return client.query(
        "MATCH (v:OntologyVersion) "
        "RETURN v.version AS version, v.content_hash AS content_hash, "
        "v.complete AS complete, v.materialised_at AS materialised_at "
        "ORDER BY v.materialised_at DESC"
    )


def version_classes(
    client: BoltClient,
    version: str,
    layer: str | None = None,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    """Classes in a version, optionally narrowed to one layer or kind."""
    return client.query(
        "MATCH (c:Class {version: $version}) "
        "WHERE ($layer IS NULL OR c.layer = $layer) "
        "  AND ($kind IS NULL OR c.kind = $kind) "
        "RETURN c.key AS name, c.layer AS layer, c.kind AS kind, "
        "c.abstract AS abstract, c.description AS description "
        "ORDER BY c.key",
        {"version": version, "layer": layer, "kind": kind},
    )


def version_hierarchy(client: BoltClient, version: str) -> list[dict[str, Any]]:
    """The is_a edges, for the hierarchy view."""
    return client.query(
        "MATCH (child:Class {version: $version})-[:IS_A]->(parent:Class {version: $version}) "
        "RETURN child.key AS child, parent.key AS parent "
        "ORDER BY parent.key, child.key",
        {"version": version},
    )


def version_relationships(client: BoltClient, version: str) -> list[dict[str, Any]]:
    """Class-to-class edges implied by attribute ranges, for the relationship view."""
    return client.query(
        "MATCH (owner:Class {version: $version})-[:HAS_ATTRIBUTE]->"
        "(a:Attribute {version: $version})-[:RANGE]->(target:Class {version: $version}) "
        "RETURN owner.key AS source, a.key AS attribute, target.key AS target, "
        "a.multivalued AS multivalued "
        "ORDER BY owner.key, a.key",
        {"version": version},
    )


def neighbourhood(
    client: BoltClient, version: str, focus: str, depth: int = 1
) -> list[dict[str, Any]]:
    """Classes within `depth` is_a hops of a focus class, for focused views."""
    if not 1 <= depth <= _MAX_DEPTH:
        raise ValueError(f"depth must be between 1 and {_MAX_DEPTH}")
    # depth is a validated int, so interpolating it cannot inject Cypher.
    return client.query(
        f"MATCH path = (c:Class {{version: $version, key: $focus}})"
        f"-[:IS_A *1..{depth}]-(related:Class {{version: $version}}) "
        f"RETURN DISTINCT related.key AS name, related.layer AS layer, "
        f"related.kind AS kind, size(path) AS distance "
        f"ORDER BY distance, related.key",
        {"version": version, "focus": focus},
    )


def class_detail(client: BoltClient, version: str, name: str) -> dict[str, Any] | None:
    """Everything the inspector shows for one class."""
    rows = client.query(
        "MATCH (c:Class {version: $version, key: $name}) "
        "RETURN c.key AS name, c.layer AS layer, c.kind AS kind, "
        "c.abstract AS abstract, c.description AS description",
        {"version": version, "name": name},
    )
    if not rows:
        return None
    detail = rows[0]

    detail["parents"] = [
        row["name"]
        for row in client.query(
            "MATCH (:Class {version: $version, key: $name})-[:IS_A]->(p:Class) "
            "RETURN p.key AS name ORDER BY p.key",
            {"version": version, "name": name},
        )
    ]
    detail["children"] = [
        row["name"]
        for row in client.query(
            "MATCH (c:Class)-[:IS_A]->(:Class {version: $version, key: $name}) "
            "RETURN c.key AS name ORDER BY c.key",
            {"version": version, "name": name},
        )
    ]
    # HAS_ATTRIBUTE covers declared and inherited; `owner` says which class declared it.
    detail["attributes"] = client.query(
        "MATCH (:Class {version: $version, key: $name})-[:HAS_ATTRIBUTE]->(a:Attribute) "
        "OPTIONAL MATCH (a)-[:RANGE]->(r) "
        "RETURN a.key AS name, a.range AS range, a.multivalued AS multivalued, "
        "a.owner AS owner, (a.owner = $name) AS declared, labels(r)[0] AS range_label "
        "ORDER BY a.key",
        {"version": version, "name": name},
    )
    detail["ossie_mappings"] = client.query(
        "MATCH (e:OssieElement {version: $version})-[m:MAPS_TO]->"
        "(:Class {version: $version, key: $name}) "
        "RETURN e.key AS element, e.model AS model, m.identifiers AS identifiers, "
        "m.resolution AS resolution "
        "ORDER BY e.key",
        {"version": version, "name": name},
    )
    detail["glossary_terms"] = client.query(
        "MATCH (t:GlossaryTerm {version: $version})-[:MAPS_TO]->"
        "(:Class {version: $version, key: $name}) "
        "RETURN t.key AS term, t.description AS description ORDER BY t.key",
        {"version": version, "name": name},
    )
    return detail


def broken_mappings(client: BoltClient, version: str) -> list[dict[str, Any]]:
    """Ossie elements whose MAPS_TO target is absent, for the viewer's warnings.

    The parser records the intended target name even when it cannot resolve it,
    so a mapping pointing at a class that does not exist shows up here rather
    than vanishing.
    """
    return client.query(
        "MATCH (e:OssieElement {version: $version}) "
        "WHERE e.unresolved_target IS NOT NULL "
        "RETURN e.key AS element, e.model AS model, "
        "e.unresolved_target AS missing_class ORDER BY e.key",
        {"version": version},
    )
