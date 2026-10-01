"""Template registry (spec: "Template architecture").

Templates live in ``templates/<artifact_type>/<template_id>/`` with a
``template.yaml`` descriptor plus any renderer assets. A template's content hash
covers every file in its directory, so changing any byte changes the template
hash, the bundle hash and therefore the dataset_id.
"""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from .config import ARTIFACT_TYPES, SCENARIO_TYPES
from .ids import hash_parts

TEMPLATE_SCHEMA_VERSION = "1.0"
DESCRIPTOR = "template.yaml"


def default_templates_dir() -> Path:
    override = os.environ.get("HELIOS_DS_TEMPLATES_DIR")
    return Path(override) if override else Path(__file__).resolve().parents[2] / "templates"


class TemplateSpec(BaseModel):
    """Contents of template.yaml. Unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid")

    template_id: str
    template_version: str
    template_schema_version: Literal["1.0"]
    artifact_type: str
    supported_scenario_types: List[str]
    required_fields: List[str]
    optional_fields: List[str] = []
    rendering_parameters: Dict[str, Any] = {}
    ground_truth_locator_strategy: str

    @field_validator("artifact_type")
    @classmethod
    def _known_artifact_type(cls, value: str) -> str:
        if value not in ARTIFACT_TYPES:
            raise ValueError(f"unknown artifact_type {value!r}")
        return value

    @field_validator("supported_scenario_types")
    @classmethod
    def _known_scenarios(cls, value: List[str]) -> List[str]:
        unknown = sorted(set(value) - set(SCENARIO_TYPES))
        if unknown or not value:
            raise ValueError(f"supported_scenario_types must be non-empty; unknown: {unknown}")
        return value


@dataclass(frozen=True)
class Template:
    spec: TemplateSpec
    content_hash: str
    path: Path

    @property
    def template_id(self) -> str:
        return self.spec.template_id

    @property
    def template_version(self) -> str:
        return self.spec.template_version

    @property
    def has_renderer(self) -> bool:
        """Whether the template directory has a ``renderer.py`` yet."""
        return (self.path / "renderer.py").is_file()


def content_hash(directory: Path) -> str:
    """SHA-256 over (relative path, file SHA-256) for every file, sorted by path."""
    entries = []
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        if "__pycache__" in path.parts:
            continue
        entries.append(
            [path.relative_to(directory).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()]
        )
    return hash_parts(entries)


class TemplateRegistry:
    def __init__(self, templates: Dict[str, Template]):
        self.templates = templates

    @classmethod
    def load(cls, root: Optional[Path] = None) -> "TemplateRegistry":
        root = root or default_templates_dir()
        templates: Dict[str, Template] = {}
        for descriptor in sorted(root.glob(f"*/*/{DESCRIPTOR}")):
            directory = descriptor.parent
            spec = TemplateSpec.model_validate(yaml.safe_load(descriptor.read_text()))
            where = directory.relative_to(root).as_posix()
            if (directory.parent.name, directory.name) != (spec.artifact_type, spec.template_id):
                raise ValueError(f"{where}: directory must be <artifact_type>/<template_id>")
            if spec.template_id in templates:
                raise ValueError(f"duplicate template_id {spec.template_id!r}")
            templates[spec.template_id] = Template(spec, content_hash(directory), directory)
        if not templates:
            raise ValueError(f"no templates found under {root}")
        return cls(templates)

    def get(self, template_id: str) -> Template:
        try:
            return self.templates[template_id]
        except KeyError:
            raise KeyError(f"unknown template {template_id!r}") from None

    def bundle_hash(self) -> str:
        return hash_parts(
            sorted(
                [t.template_id, t.template_version, t.content_hash] for t in self.templates.values()
            )
        )

    def describe(self) -> List[Dict[str, str]]:
        """Stable, sorted template identities for manifests and template_versions rows."""
        return [
            {
                "template_id": t.template_id,
                "template_version": t.template_version,
                "template_schema_version": t.spec.template_schema_version,
                "artifact_type": t.spec.artifact_type,
                "content_hash": t.content_hash,
            }
            for t in sorted(self.templates.values(), key=lambda t: t.template_id)
        ]
