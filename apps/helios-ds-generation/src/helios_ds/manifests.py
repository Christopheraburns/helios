"""Generation identity and the canonical generation manifest.

dataset_id = UUIDv5 over the *generation identity*: canonical config hash,
template bundle hash, TPC-DS source fingerprint hash, generator version and ID
schema version. Changing any of them yields a different dataset.

The manifest is serialized as canonical JSON (sorted keys, no whitespace) and
contains no wall-clock values, so equivalent runs produce byte-identical
manifests. It includes scenario plans, so it is generator-internal: it is stored
under ``_manifests/`` and in helios_ds, neither of which the crawler may read.
"""

import json
from typing import Any, Dict, List, Literal

from pydantic import BaseModel

from . import __version__
from .ids import GENERATOR_SCHEMA_VERSION, dataset_id, hash_parts
from .object_store import MANIFEST_PREFIX
from .scenarios import ScenarioPlan

MANIFEST_SCHEMA_VERSION = "1.0"  # keep in sync with GenerationManifest.manifest_schema_version


class GenerationIdentity(BaseModel):
    config_hash: str
    template_bundle_hash: str
    source_fingerprint_hash: str
    generator_version: str = __version__
    generator_schema_version: str = GENERATOR_SCHEMA_VERSION

    def dataset_id(self) -> str:
        return dataset_id(hash_parts("generation_identity", self.model_dump()))


class GenerationManifest(BaseModel):
    manifest_schema_version: Literal["1.0"] = "1.0"
    dataset_id: str
    identity: GenerationIdentity
    config: Dict[str, Any]
    source_fingerprint: Dict[str, Any]
    templates: List[Dict[str, str]]
    artifact_counts: Dict[str, Dict[str, int]]
    scenario_counts: Dict[str, Dict[str, int]]
    scenarios: List[ScenarioPlan]

    def to_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> "GenerationManifest":
        return cls.model_validate_json(data)


def manifest_key(dataset_id: str) -> str:
    return f"{MANIFEST_PREFIX}/{dataset_id}/generation-manifest.json"
