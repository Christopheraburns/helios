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
from .mapping import (
    ClassIdentifiers,
    ResolutionConfig,
    SourceMapping,
    load_mapping,
    load_mappings,
    resolution_config,
)

__all__ = [
    "EDGE_TYPES",
    "LAYERS",
    "NODE_LABELS",
    "ClassIdentifiers",
    "GraphEdge",
    "GraphNode",
    "OntologyGraph",
    "OntologyGraphError",
    "ResolutionConfig",
    "SourceMapping",
    "load_mapping",
    "load_mappings",
    "resolution_config",
]
