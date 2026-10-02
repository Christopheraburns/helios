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
from typing import Any

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


class SourceMapping(_Model):
    model: str
    ontology_version: str
    entities: list[EntityMapping] = Field(default_factory=list)
    relationships: list[RelationshipMapping] = Field(default_factory=list)
    concepts: list[ConceptMapping] = Field(default_factory=list)
    resolution: Resolution = Field(default_factory=Resolution)


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
    classes: dict[str, ClassIdentifiers]
    scope: list[ResolutionScope]
    thresholds: Thresholds
    relationships: dict[str, str] = Field(default_factory=dict)

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
        classes=classes,
        scope=list(mapping.resolution.scope),
        thresholds=mapping.resolution.thresholds,
        relationships={r.ossie_relationship: r.edge for r in mapping.relationships},
    )


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
