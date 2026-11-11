"""Source-to-ontology mappings (ontology/mappings/*), typed.

A mapping binds one source model's elements (Ossie datasets, relationships,
metrics) to ontology classes and says which columns identify instances. Two
readers use it:

- the parser, which adds the mapping to the ontology graph (OssieElement nodes,
  MAPS_TO / MATERIALISES_AS edges) and flags elements missing from the model;
- the crawler's resolver, which reads only ``resolution_config()``: per class, the
  identifier columns to build its dictionaries from, plus the tier thresholds.

Shape follows ontology/mappings/mapping.schema.yaml (class SourceMapping).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class Identifiers(_Model):
    primary: list[str]
    secondary: list[str] = Field(default_factory=list)
    display: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    # Composite names, e.g. "{c_salutation} {c_last_name}" (see template_columns).
    alias_templates: list[str] = Field(default_factory=list)


_TEMPLATE_FIELD = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def template_columns(template: str) -> list[str]:
    """Columns referenced by an alias template, in order."""
    return _TEMPLATE_FIELD.findall(template)


class AttributeBinding(_Model):
    attribute: str
    columns: list[str]


class EntityMapping(_Model):
    ossie_element: str
    class_name: str = Field(alias="class")
    identifiers: Identifiers
    attributes: list[AttributeBinding] = Field(default_factory=list)


class RelationshipMapping(_Model):
    ossie_relationship: str
    edge: str


class ConceptMapping(_Model):
    glossary_term: str
    class_name: str = Field(alias="class")
    ossie_element: str


class ResolutionScope(_Model):
    source_pattern: str
    candidate_classes: list[str]


class Thresholds(_Model):
    alias_min_score: float = 0.92
    fuzzy_min_score: float = 0.85
    model_assisted_min_confidence: float = 0.80


class Resolution(_Model):
    scope: list[ResolutionScope] = Field(default_factory=list)
    thresholds: Thresholds = Field(default_factory=Thresholds)


# --- record anchors (CG-4) ----------------------------------------------------------
# An anchor is a class whose warehouse row a group of documents (a case) is
# about. It says how values in the documents point at that row, and what the
# row names once found. All tables and columns are the mapped classes' own; the
# crawler builds one bounded query from this and never free-form SQL.


class DateSource(_Model):
    """Where a record's date is: a column of its table, or, when ``table`` is
    given, a foreign key ``column`` into a date table whose ``key`` it matches
    and whose ``value`` column holds the date."""

    column: str
    table: str | None = None
    key: str | None = None
    value: str | None = None


class Lookup(_Model):
    """A value found in documents that constrains a column of the anchor's row."""

    column: str
    match: Literal["equals", "ends_with"] = "equals"
    type: Literal["integer", "string"] = "integer"
    # A key pattern or label supplies this value when it looks values up in one
    # of these columns. Default: the column itself.
    source_columns: list[str] = Field(default_factory=list)
    # The class a mention of this value refers to when it proposes none of the
    # case's records itself (a receipt number identifies the sale).
    identifies: str | None = None


class AnchorJoin(_Model):
    """A foreign key of a record's row: the entity it names and the relationship that makes."""

    column: str
    class_name: str = Field(alias="class")
    edge: str


class ColocatedClass(_Model):
    """A class living on a joined table under another key (a brand on the item row)."""

    via: str = Field(description="The joined class whose table it is on")
    class_name: str = Field(alias="class")


class RelatedRecord(_Model):
    """Another record of the same case, joined to the anchor on key columns."""

    class_name: str = Field(alias="class")
    # Not "on": YAML reads that bare word as the boolean true.
    join_on: list[tuple[str, str]] = Field(
        min_length=1, description="[anchor column, this record's column] pairs"
    )
    edge: str = Field(description="Relationship from the anchor to this record")
    joins: list[AnchorJoin] = Field(default_factory=list)
    date: DateSource | None = None
    # The documents name one party and place for the whole case: where the anchor
    # row names a class, this record's relationships use the anchor's entity.
    share_anchor_entities: bool = False


class Anchor(_Model):
    class_name: str = Field(alias="class")
    lookups: list[Lookup] = Field(default_factory=list)
    joins: list[AnchorJoin] = Field(default_factory=list)
    colocated: list[ColocatedClass] = Field(default_factory=list)
    date: DateSource | None = None
    related: list[RelatedRecord] = Field(default_factory=list)
    row_limit: int = Field(5, ge=1, le=100, description="More consistent rows than this settle nothing")
    min_constraint_kinds: int = Field(
        2, ge=1, description="How many kinds of evidence a case needs before the warehouse is asked"
    )


class SourceMapping(_Model):
    model: str
    ontology_version: str
    # The warehouse database (schema) the mapped tables are in. When empty it is
    # taken from the model's name up to its first dot ("sales.ossie.yaml" -> "sales").
    database: str = ""
    entities: list[EntityMapping] = Field(default_factory=list)
    relationships: list[RelationshipMapping] = Field(default_factory=list)
    concepts: list[ConceptMapping] = Field(default_factory=list)
    resolution: Resolution = Field(default_factory=Resolution)
    anchors: list[Anchor] = Field(default_factory=list)


def load_mapping(path: str | Path) -> SourceMapping:
    return SourceMapping.model_validate(yaml.safe_load(Path(path).read_text()))


def load_mappings(directory: str | Path) -> list[SourceMapping]:
    """Every mapping file under ``directory`` (recursively), in path order."""
    return [load_mapping(p) for p in sorted(Path(directory).rglob("*.yaml"))]


class ClassIdentifiers(_Model):
    """What the resolver needs to find instances of one class in a source."""

    class_name: str
    ossie_element: str
    primary: list[str]
    secondary: list[str]
    display: list[str]
    aliases: list[str]
    alias_templates: list[str]


class ResolutionConfig(_Model):
    """The resolver's view of a mapping: identifiers per class, scopes, thresholds,
    and the source's join paths (``relationships``: Ossie relationship name ->
    ontology edge), which joint resolution follows instead of hand-written SQL."""

    model: str
    ontology_version: str
    database: str
    classes: dict[str, ClassIdentifiers]
    scope: list[ResolutionScope]
    thresholds: Thresholds
    relationships: dict[str, str] = Field(default_factory=dict)
    anchors: list[Anchor] = Field(default_factory=list)

    def candidate_classes(self, source_uri: str) -> list[str]:
        """Classes to look for in an asset at ``source_uri`` (all mapped, if no scope matches)."""
        from fnmatch import fnmatch

        for scope in self.scope:
            if fnmatch(source_uri, scope.source_pattern):
                return list(scope.candidate_classes)
        return sorted(self.classes)


def resolution_config(mapping: SourceMapping) -> ResolutionConfig:
    classes: dict[str, ClassIdentifiers] = {}
    for entity in mapping.entities:
        if entity.class_name in classes:
            raise ValueError(f"{mapping.model}: class {entity.class_name} is mapped twice")
        ids = entity.identifiers
        classes[entity.class_name] = ClassIdentifiers(
            class_name=entity.class_name,
            ossie_element=entity.ossie_element,
            primary=list(ids.primary),
            secondary=list(ids.secondary),
            display=list(ids.display),
            aliases=list(ids.aliases),
            alias_templates=list(ids.alias_templates),
        )
    return ResolutionConfig(
        model=mapping.model,
        ontology_version=mapping.ontology_version,
        database=mapping.database or mapping.model.split(".", 1)[0],
        classes=classes,
        scope=list(mapping.resolution.scope),
        thresholds=mapping.resolution.thresholds,
        relationships={r.ossie_relationship: r.edge for r in mapping.relationships},
        anchors=list(mapping.anchors),
    )


_PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def mapping_problems(
    mapping: SourceMapping,
    ossie_model: dict[str, Any] | None = None,
    ontology_classes: set[str] | None = None,
) -> list[str]:
    """Why ``mapping`` cannot be used, in words a person can act on; [] if it can.

    Always checked: each class mapped once, a key on every class, plain column
    names. With ``ossie_model`` (the published semantic model): every table,
    column, relationship and metric named exists in it. With
    ``ontology_classes`` (the class names of a published ontology): every class
    and relationship type named exists in it.
    """
    problems: list[str] = []
    datasets = {d["name"]: d for d in (ossie_model or {}).get("datasets", [])}
    known = ossie_elements(ossie_model) if ossie_model else None
    seen: set[str] = set()
    for index, entity in enumerate(mapping.entities):
        where = f"entities[{index}] ({entity.class_name})"
        if entity.class_name in seen:
            problems.append(f"{where}: class {entity.class_name} is mapped more than once")
        seen.add(entity.class_name)
        ids = entity.identifiers
        if not ids.primary:
            problems.append(f"{where}: needs at least one primary key column")
        columns = [
            *ids.primary,
            *ids.secondary,
            *ids.display,
            *ids.aliases,
            *(c for t in ids.alias_templates for c in template_columns(t)),
            *(c for a in entity.attributes for c in a.columns),
        ]
        for template in ids.alias_templates:
            if not template_columns(template):
                problems.append(f"{where}: alias template {template!r} names no column")
        for column in sorted(set(columns)):
            if not _PLAIN.match(column):
                problems.append(f"{where}: {column!r} is not a plain column name")
        if ontology_classes is not None and entity.class_name not in ontology_classes:
            problems.append(f"{where}: class {entity.class_name} is not in the ontology")
        if known is None:
            continue
        dataset = datasets.get(entity.ossie_element)
        if dataset is None:
            problems.append(f"{where}: table {entity.ossie_element!r} is not in the semantic model")
            continue
        fields = {f["name"] for f in dataset.get("fields", [])}
        for column in sorted(set(columns) - fields):
            if _PLAIN.match(column):
                problems.append(
                    f"{where}: column {column!r} is not in table {entity.ossie_element!r}"
                )
    for index, relationship in enumerate(mapping.relationships):
        where = f"relationships[{index}] ({relationship.ossie_relationship})"
        if known is not None and known.get(relationship.ossie_relationship) != "relationship":
            problems.append(f"{where}: is not a relationship in the semantic model")
        if ontology_classes is not None and relationship.edge not in ontology_classes:
            problems.append(f"{where}: relationship type {relationship.edge} is not in the ontology")
    for index, concept in enumerate(mapping.concepts):
        where = f"concepts[{index}] ({concept.glossary_term})"
        if known is not None and concept.ossie_element not in known:
            problems.append(f"{where}: {concept.ossie_element!r} is not in the semantic model")
        if ontology_classes is not None and concept.class_name not in ontology_classes:
            problems.append(f"{where}: class {concept.class_name} is not in the ontology")
    for index, scope in enumerate(mapping.resolution.scope):
        for class_name in scope.candidate_classes:
            if class_name not in seen:
                problems.append(
                    f"resolution.scope[{index}]: class {class_name} has no entity mapping"
                )
    problems += _anchor_problems(mapping, datasets if known is not None else None, ontology_classes)
    if mapping.database and not _PLAIN.match(mapping.database):
        problems.append(f"database: {mapping.database!r} is not a plain database name")
    thresholds = mapping.resolution.thresholds
    for name in ("alias_min_score", "fuzzy_min_score", "model_assisted_min_confidence"):
        if not 0.0 <= getattr(thresholds, name) <= 1.0:
            problems.append(f"resolution.thresholds.{name}: must be between 0 and 1")
    return problems


def _anchor_problems(
    mapping: SourceMapping,
    datasets: dict[str, Any] | None,
    ontology_classes: set[str] | None,
) -> list[str]:
    """Problems in the anchors: classes that are not mapped, columns that are
    not in their tables, relationship types the ontology lacks."""
    problems: list[str] = []
    entities = {e.class_name: e for e in mapping.entities}

    def table_of(where: str, class_name: str) -> str | None:
        entity = entities.get(class_name)
        if entity is None:
            problems.append(f"{where}: class {class_name} has no entity mapping")
            return None
        return entity.ossie_element

    def column(where: str, table: str | None, name: str) -> None:
        if not _PLAIN.match(name):
            problems.append(f"{where}: {name!r} is not a plain column name")
        elif datasets is not None and table is not None and table in datasets:
            if name not in {f["name"] for f in datasets[table].get("fields", [])}:
                problems.append(f"{where}: column {name!r} is not in table {table!r}")

    def edge(where: str, name: str) -> None:
        if ontology_classes is not None and name not in ontology_classes:
            problems.append(f"{where}: relationship type {name} is not in the ontology")

    def date(where: str, table: str | None, source: DateSource | None) -> None:
        if source is None:
            return
        column(f"{where}.date", table, source.column)
        if source.table is None:
            if source.key or source.value:
                problems.append(f"{where}.date: key and value need the date table")
            return
        if not (source.key and source.value):
            problems.append(f"{where}.date: a date table needs its key and value columns")
            return
        if datasets is not None and source.table not in datasets:
            problems.append(f"{where}.date: table {source.table!r} is not in the semantic model")
        column(f"{where}.date", source.table, source.key)
        column(f"{where}.date", source.table, source.value)

    def joins(where: str, table: str | None, found: list[AnchorJoin]) -> None:
        for index, join in enumerate(found):
            here = f"{where}.joins[{index}] ({join.class_name})"
            column(here, table, join.column)
            target = entities.get(join.class_name)
            if target is None:
                problems.append(f"{here}: class {join.class_name} has no entity mapping")
            elif len(target.identifiers.primary) != 1:
                problems.append(f"{here}: class {join.class_name} needs a single-column key to be joined to")
            edge(here, join.edge)

    seen: set[str] = set()
    for number, anchor in enumerate(mapping.anchors):
        where = f"anchors[{number}] ({anchor.class_name})"
        if anchor.class_name in seen:
            problems.append(f"{where}: class {anchor.class_name} is an anchor more than once")
        seen.add(anchor.class_name)
        table = table_of(where, anchor.class_name)
        for index, lookup in enumerate(anchor.lookups):
            column(f"{where}.lookups[{index}]", table, lookup.column)
            if lookup.identifies and lookup.identifies not in {
                anchor.class_name,
                *(r.class_name for r in anchor.related),
            }:
                problems.append(
                    f"{where}.lookups[{index}]: identifies {lookup.identifies}, which is neither "
                    "the anchor nor one of its related records"
                )
        joins(where, table, anchor.joins)
        date(where, table, anchor.date)
        joined = {j.class_name for j in anchor.joins}
        for index, item in enumerate(anchor.colocated):
            here = f"{where}.colocated[{index}] ({item.class_name})"
            if item.via not in joined:
                problems.append(f"{here}: via {item.via}, which is not one of the anchor's joins")
            own, via = entities.get(item.class_name), entities.get(item.via)
            if own is None:
                problems.append(f"{here}: class {item.class_name} has no entity mapping")
            elif via is not None and own.ossie_element != via.ossie_element:
                problems.append(
                    f"{here}: {item.class_name} is in table {own.ossie_element!r}, not "
                    f"{item.via}'s table {via.ossie_element!r}"
                )
        for index, related in enumerate(anchor.related):
            here = f"{where}.related[{index}] ({related.class_name})"
            related_table = table_of(here, related.class_name)
            for ours, theirs in related.join_on:
                column(f"{here}.join_on", table, ours)
                column(f"{here}.join_on", related_table, theirs)
            edge(here, related.edge)
            joins(here, related_table, related.joins)
            date(here, related_table, related.date)
    return problems


def ossie_elements(model: dict[str, Any]) -> dict[str, str]:
    """Element name -> kind (dataset, relationship, metric) for an Ossie model dict.
    Metrics are addressed as ``metrics.<name>``, as in mapping concepts."""
    elements: dict[str, str] = {}
    for dataset in model.get("datasets", []):
        elements[dataset["name"]] = "dataset"
    for relationship in model.get("relationships", []):
        elements[relationship["name"]] = "relationship"
    for metric in model.get("metrics", []):
        elements[f"metrics.{metric['name']}"] = "metric"
    return elements
