"""Parse LinkML schemas into OntologyGraph payloads.

Transforms a root LinkML schema (with its full import chain resolved by
SchemaView) into canonical Class, Attribute, Enum, EnumValue nodes and
IS_A, HAS_ATTRIBUTE, RANGE edges, with deterministic content_hash.

SchemaView resolves:
  - Imports (../ relative paths)
  - Inherited slots (is_a chains crossed layer boundaries)
  - Enum permissible values

Mappings (ontology/mappings/*) are added as OssieElement and GlossaryTerm
nodes with MAPS_TO / MATERIALISES_AS edges; elements missing from the Ossie
model, unknown classes and version mismatches are reported as broken_mappings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from linkml_runtime.utils.schemaview import SchemaView

from helios_core.ontology.graph import GraphEdge, GraphNode, OntologyGraph
from helios_core.ontology.mapping import SourceMapping, ossie_elements


@dataclass
class ParseResult:
    """Outcome of parsing a LinkML schema."""

    graph: OntologyGraph
    broken_mappings: list[dict[str, str]]


def parse(
    schema_yaml_path: str,
    version: str = "0.1.0",
    ossie_model: dict[str, Any] | None = None,
    mappings: list[SourceMapping] | None = None,
) -> ParseResult:
    """Parse a LinkML schema into an OntologyGraph.

    Args:
        schema_yaml_path: Path to the root LinkML schema (e.g., extension.yaml).
                         SchemaView will resolve all imports.
        version: Ontology version string (e.g., "0.1.0"). Included in the graph.
        ossie_model: Optional Ossie model (the published model YAML as a dict).
                    If provided, mapped elements are checked against it and
                    missing ones recorded as broken.
        mappings: Source mappings (ontology/mappings/*). Each one whose
                  ontology_version is in the schema's import chain is added to
                  the graph as OssieElement / GlossaryTerm nodes with MAPS_TO and
                  MATERIALISES_AS edges.

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

        nodes.append(
            GraphNode(
                label="Class",
                key=class_name,
                properties={
                    "layer": _layer(cls.from_schema),
                    "kind": _kind(view, class_name),
                    "abstract": bool(cls.abstract),
                    "description": (cls.description or "").strip(),
                },
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
                        "range": slot.range or "",
                        # The class that declares the slot; induced copies on
                        # subclasses are marked inherited, so viewers draw each
                        # attribute once.
                        "declared_by": _declared_by(view, class_name, slot.domain_of),
                        "inherited": class_name not in (slot.domain_of or []),
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

    _add_mappings(view, mappings or [], ossie_model, nodes, edges, broken)

    graph = OntologyGraph(
        version=version,
        nodes=tuple(nodes),
        edges=tuple(edges),
    )

    return ParseResult(graph=graph, broken_mappings=broken)


# Ontology layer by the schema a class is defined in (core / packs/<domain> /
# customers/<tenant>), matching the layout in ontology/README.md.
def _layer(from_schema: str | None) -> str:
    uri = from_schema or ""
    if "/customers/" in uri:
        return "customer"
    if "/packs/" in uri:
        return "pack"
    return "core"


# The core branch a class belongs to, most specific first.
_KINDS = (
    ("Relationship", "relationship"),
    ("InformationAsset", "asset"),
    ("Segment", "asset"),
    ("Event", "event"),
    ("EnterpriseEntity", "entity"),
    ("SemanticConcept", "concept"),
    ("Claim", "claim"),
    ("OssieModelElement", "model"),
)


def _kind(view: SchemaView, class_name: str) -> str:
    ancestors = set(view.class_ancestors(class_name))
    for root, kind in _KINDS:
        if root in ancestors:
            return kind
    return "other"


def _declared_by(view: SchemaView, class_name: str, domain_of: list[str] | None) -> str:
    """The nearest ancestor (or the class itself) that declares the slot."""
    owners = set(domain_of or [])
    for ancestor in view.class_ancestors(class_name):  # self first, then up the chain
        if ancestor in owners:
            return ancestor
    return class_name



def _add_mappings(
    view: SchemaView,
    mappings: list[SourceMapping],
    ossie_model: dict[str, Any] | None,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    broken: list[dict[str, str]],
) -> None:
    """Add each applicable mapping: Ossie elements, glossary terms and their edges."""
    in_chain = {f"{s.name}@{s.version}" for s in view.schema_map.values()}
    names_in_chain = {s.name for s in view.schema_map.values()}
    classes = set(view.all_classes())
    known = ossie_elements(ossie_model) if ossie_model else None
    added: set[tuple[str, str]] = set()

    def node(label: str, key: str, properties: dict[str, Any]) -> None:
        if (label, key) not in added:
            added.add((label, key))
            nodes.append(GraphNode(label=label, key=key, properties=properties))

    def element(name: str, kind: str, mapping: SourceMapping) -> None:
        missing = known is not None and name not in known
        if missing:
            broken.append({"class": "", "mapped_to": name, "status": "not_found"})
        node(
            "OssieElement",
            name,
            {"model": mapping.model, "kind": kind, "status": "not_found" if missing else "ok"},
        )

    for mapping in mappings:
        target = mapping.ontology_version
        if target not in in_chain:
            if target.split("@")[0] in names_in_chain:
                broken.append({"class": "", "mapped_to": target, "status": "version_mismatch"})
            continue
        for entity in mapping.entities:
            if entity.class_name not in classes:
                broken.append(
                    {"class": entity.class_name, "mapped_to": entity.ossie_element,
                     "status": "unknown_class"}
                )
                continue
            element(entity.ossie_element, "dataset", mapping)
            ids = entity.identifiers
            edges.append(
                GraphEdge(
                    type="MAPS_TO",
                    from_label="OssieElement",
                    from_key=entity.ossie_element,
                    to_label="Class",
                    to_key=entity.class_name,
                    properties={
                        "primary": list(ids.primary),
                        "secondary": list(ids.secondary),
                        "display": list(ids.display),
                        "aliases": list(ids.aliases),
                        "alias_templates": list(ids.alias_templates),
                        "attributes": [
                            f"{a.attribute}={','.join(a.columns)}" for a in entity.attributes
                        ],
                    },
                )
            )
        for rel in mapping.relationships:
            if rel.edge not in classes:
                broken.append(
                    {"class": rel.edge, "mapped_to": rel.ossie_relationship, "status": "unknown_class"}
                )
                continue
            element(rel.ossie_relationship, "relationship", mapping)
            edges.append(
                GraphEdge(
                    type="MATERIALISES_AS",
                    from_label="OssieElement",
                    from_key=rel.ossie_relationship,
                    to_label="Class",
                    to_key=rel.edge,
                )
            )
        for concept in mapping.concepts:
            if concept.class_name not in classes:
                continue
            node("GlossaryTerm", concept.glossary_term, {"model": mapping.model})
            edges.append(
                GraphEdge(
                    type="MAPS_TO",
                    from_label="GlossaryTerm",
                    from_key=concept.glossary_term,
                    to_label="Class",
                    to_key=concept.class_name,
                )
            )
            element(concept.ossie_element, "metric", mapping)
            edges.append(
                GraphEdge(
                    type="MAPS_TO",
                    from_label="OssieElement",
                    from_key=concept.ossie_element,
                    to_label="GlossaryTerm",
                    to_key=concept.glossary_term,
                )
            )
