"""Ontology model shared by the parser, the graph gateway and the crawler's resolver."""

from .graph import (
    EDGE_TYPES,
    LAYERS,
    NODE_LABELS,
    GraphEdge,
    GraphNode,
    OntologyGraph,
    OntologyGraphError,
)

__all__ = [
    "EDGE_TYPES",
    "LAYERS",
    "NODE_LABELS",
    "GraphEdge",
    "GraphNode",
    "OntologyGraph",
    "OntologyGraphError",
]
