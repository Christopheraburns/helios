"""Parse LinkML schemas into OntologyGraph payloads.

Transforms a root LinkML schema (with its full import chain resolved by
SchemaView) into canonical Class, Attribute, Enum, EnumValue nodes and
IS_A, HAS_ATTRIBUTE, RANGE edges, with deterministic content_hash.

SchemaView resolves:
  - Imports (../ relative paths)
  - Inherited slots (is_a chains crossed layer boundaries)
  - Enum permissible values

The parser records mappings from classes to Ossie elements (via the
description tag), validates they exist, and flags broken_mappings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from linkml_runtime.utils.schemaview import SchemaView

from helios_core.ontology.graph import OntologyGraph, GraphNode, GraphEdge


@dataclass
class ParseResult:
    """Outcome of parsing a LinkML schema."""

    graph: OntologyGraph
    broken_mappings: list[dict[str, str]]


def parse(schema_yaml_path: str, version: str = "0.1.0", ossie_model: dict[str, Any] | None = None) -> ParseResult:
    """Parse a LinkML schema into an OntologyGraph.

    Args:
        schema_yaml_path: Path to the root LinkML schema (e.g., extension.yaml).
                         SchemaView will resolve all imports.
        version: Ontology version string (e.g., "0.1.0"). Included in the graph.
        ossie_model: Optional Ossie model spec (dict of {"entities.element_name": {...}, ...}).
                    If provided, mappings are validated and broken ones recorded.

    Returns:
        ParseResult with graph and broken_mappings list.
    """
    view = SchemaView(schema_yaml_path)

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    broken: list[dict[str, str]] = []

    # === Classes (concrete and abstract) ===
    for class_name in view.all_classes():
        cls = view.get_class(class_name)
        if cls is None:
            continue

        # Extract ossie_element mapping from description.
        # Format: "description: <text> [Ossie: entities.dim_customer]"
        ossie_element = None
        if cls.description:
            match = re.search(r"\[Ossie:\s*([a-zA-Z0-9_.]+)\]", cls.description)
            if match:
                ossie_element = match.group(1)

        nodes.append(GraphNode(label="Class", key=class_name))

        # Record the mapping (even if broken).
        if ossie_element:
            is_broken = ossie_model and ossie_element not in ossie_model
            if is_broken:
                broken.append(
                    {
                        "class": class_name,
                        "mapped_to": ossie_element,
                        "status": "not_found",
                    }
                )

            # MAPS_TO edge for the mapping.
            edges.append(
                GraphEdge(
                    type="MAPS_TO",
                    from_label="Class",
                    from_key=class_name,
                    to_label="OssieElement",
                    to_key=ossie_element,
                )
            )

        # is_a parent.
        if cls.is_a:
            edges.append(
                GraphEdge(
                    type="IS_A",
                    from_label="Class",
                    from_key=class_name,
                    to_label="Class",
                    to_key=cls.is_a,
                )
            )

    # === Attributes ===
    for class_name in view.all_classes():
        cls = view.get_class(class_name)
        if cls is None or cls.abstract:
            continue

        # Induced slots include inherited attributes.
        try:
            induced = view.class_induced_slots(class_name)
        except Exception:
            continue

        for slot in induced:
            if not slot.name:
                continue

            # Attribute node: one per class+slot, for HAS_ATTRIBUTE cardinality.
            attr_key = f"{class_name}#{slot.name}"
            nodes.append(
                GraphNode(
                    label="Attribute",
                    key=attr_key,
                    properties={
                        "slot_name": slot.name,
                        "multivalued": slot.multivalued or False,
                        "identifier": slot.identifier or False,
                    },
                )
            )

            # HAS_ATTRIBUTE edge.
            edges.append(
                GraphEdge(
                    type="HAS_ATTRIBUTE",
                    from_label="Class",
                    from_key=class_name,
                    to_label="Attribute",
                    to_key=attr_key,
                )
            )

            # If range is a class, link the attribute to its target type.
            if slot.range and slot.range in view.all_classes():
                edges.append(
                    GraphEdge(
                        type="RANGE",
                        from_label="Attribute",
                        from_key=attr_key,
                        to_label="Class",
                        to_key=slot.range,
                    )
                )

    # === Enums ===
    for enum_name in view.all_enums():
        enum = view.get_enum(enum_name)
        if enum is None:
            continue

        nodes.append(GraphNode(label="Enum", key=enum_name))

        # EnumValue nodes and edges.
        for pv_name in enum.permissible_values:
            nodes.append(GraphNode(label="EnumValue", key=f"{enum_name}#{pv_name}"))
            edges.append(
                GraphEdge(
                    type="MATERIALISES_AS",
                    from_label="Enum",
                    from_key=enum_name,
                    to_label="EnumValue",
                    to_key=f"{enum_name}#{pv_name}",
                )
            )

    graph = OntologyGraph(
        version=version,
        nodes=tuple(nodes),
        edges=tuple(edges),
    )

    return ParseResult(graph=graph, broken_mappings=broken)
