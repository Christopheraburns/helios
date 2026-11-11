"""The normalised ontology graph: the contract between parser and gateway.

The O-2 parser reads LinkML with SchemaView and emits one of these; the Helios
Graph gateway loads it into Memgraph. Neither side knows the other's internals,
and the crawler's resolver reuses the same shape.

Node labels and edge types are a closed set. Cypher cannot parameterise a label
or a relationship type, so the loader interpolates them -- which is safe only
because every value is checked against the whitelists here first. Everything
else travels as a query parameter.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

NODE_LABELS: frozenset[str] = frozenset(
    {
        "OntologyVersion",
        "Class",
        "Attribute",
        "Enum",
        "EnumValue",
        "OssieElement",
        "GlossaryTerm",
    }
)

EDGE_TYPES: frozenset[str] = frozenset(
    {
        "IS_A",
        "HAS_ATTRIBUTE",
        "RANGE",
        "MAPS_TO",
        "MATERIALISES_AS",
    }
)

LAYERS: frozenset[str] = frozenset({"core", "pack", "customer"})


class OntologyGraphError(ValueError):
    """The normalised graph is malformed or references something unknown."""


@dataclass(frozen=True)
class GraphNode:
    label: str
    key: str  # natural key, unique within (version, label)
    properties: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.label not in NODE_LABELS:
            raise OntologyGraphError(
                f"unknown node label {self.label!r}; expected one of {sorted(NODE_LABELS)}"
            )
        if not self.key:
            raise OntologyGraphError(f"{self.label} node has an empty key")


@dataclass(frozen=True)
class GraphEdge:
    type: str
    from_label: str
    from_key: str
    to_label: str
    to_key: str
    properties: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in EDGE_TYPES:
            raise OntologyGraphError(
                f"unknown edge type {self.type!r}; expected one of {sorted(EDGE_TYPES)}"
            )
        for label in (self.from_label, self.to_label):
            if label not in NODE_LABELS:
                raise OntologyGraphError(f"unknown node label {label!r} on {self.type} edge")


@dataclass(frozen=True)
class OntologyGraph:
    """A whole published ontology version, ready to load."""

    version: str
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]

    def __post_init__(self) -> None:
        if not self.version:
            raise OntologyGraphError("version must not be empty")
        seen: set[tuple[str, str]] = set()
        for node in self.nodes:
            identity = (node.label, node.key)
            if identity in seen:
                raise OntologyGraphError(f"duplicate node {node.label}:{node.key}")
            seen.add(identity)
        for edge in self.edges:
            for label, key in (
                (edge.from_label, edge.from_key),
                (edge.to_label, edge.to_key),
            ):
                if (label, key) not in seen:
                    raise OntologyGraphError(
                        f"{edge.type} edge references missing node {label}:{key}"
                    )

    @property
    def content_hash(self) -> str:
        """SHA-256 over the canonical form: the same files always hash the same.

        Ordering and property order are normalised first, so a parser that
        happens to emit nodes in a different sequence still produces the same
        version. This is what makes republishing idempotent (O-2).
        """
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    def canonical_json(self) -> str:
        payload = {
            "version": self.version,
            "nodes": sorted(
                ({"label": n.label, "key": n.key, "properties": n.properties} for n in self.nodes),
                key=lambda n: (n["label"], n["key"]),
            ),
            "edges": sorted(
                (
                    {
                        "type": e.type,
                        "from_label": e.from_label,
                        "from_key": e.from_key,
                        "to_label": e.to_label,
                        "to_key": e.to_key,
                        "properties": e.properties,
                    }
                    for e in self.edges
                ),
                key=lambda e: (
                    e["type"],
                    e["from_label"],
                    e["from_key"],
                    e["to_label"],
                    e["to_key"],
                ),
            ),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def nodes_by_label(self) -> dict[str, list[GraphNode]]:
        grouped: dict[str, list[GraphNode]] = {}
        for node in self.nodes:
            grouped.setdefault(node.label, []).append(node)
        return grouped

    def edges_by_type(self) -> dict[str, list[GraphEdge]]:
        grouped: dict[str, list[GraphEdge]] = {}
        for edge in self.edges:
            grouped.setdefault(edge.type, []).append(edge)
        return grouped

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "content_hash": self.content_hash,
            "nodes": [
                {"label": n.label, "key": n.key, "properties": n.properties} for n in self.nodes
            ],
            "edges": [
                {
                    "type": e.type,
                    "from_label": e.from_label,
                    "from_key": e.from_key,
                    "to_label": e.to_label,
                    "to_key": e.to_key,
                    "properties": e.properties,
                }
                for e in self.edges
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> OntologyGraph:
        """Rebuild from to_dict output, verifying the hash if one was recorded."""
        graph = cls(
            version=payload["version"],
            nodes=tuple(
                GraphNode(n["label"], n["key"], n.get("properties") or {})
                for n in payload.get("nodes", ())
            ),
            edges=tuple(
                GraphEdge(
                    e["type"],
                    e["from_label"],
                    e["from_key"],
                    e["to_label"],
                    e["to_key"],
                    e.get("properties") or {},
                )
                for e in payload.get("edges", ())
            ),
        )
        recorded = payload.get("content_hash")
        if recorded and recorded != graph.content_hash:
            raise OntologyGraphError(
                f"content hash mismatch for version {graph.version}: "
                f"recorded {recorded}, computed {graph.content_hash}"
            )
        return graph
