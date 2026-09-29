"""Configuration schema for Helios-DS generation.

Every model forbids unknown fields, so a config written for a different schema
fails validation with the offending field named, instead of being silently
ignored. ``DatasetConfig.config_hash()`` is the canonical identity of a config
and feeds ``ids.dataset_id``.
"""

import hashlib
import json
import math
from enum import Enum
from typing import Dict, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONFIG_SCHEMA_VERSION = "1.0"  # keep in sync with DatasetConfig.config_schema_version

ARTIFACT_TYPES = ("pdf", "image", "email", "chat", "audio", "video")

SCENARIO_TYPES = (
    "product_return_damage",
    "warehouse_inventory_issue",
    "promotion_performance",
    "customer_complaint",
)


class DifficultyProfile(str, Enum):
    """Difficulty tiers for generated artifacts."""

    DIRECT_IDENTIFIER = "direct_identifier"
    ALIAS = "alias"
    CONTEXTUAL = "contextual"
    MULTIMODAL = "multimodal"
    CROSS_SOURCE = "cross_source"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtifactConfig(_StrictModel):
    """Configuration for a single artifact type."""

    enabled: bool = True
    target_count: int = Field(..., ge=0)


class ScenarioConfig(_StrictModel):
    """Configuration for a scenario type.

    Weights are relative: they need not sum to 1 (the spec's example sums to 0.6).
    Use ``DatasetConfig.normalized_scenario_weights()`` for proportions.
    """

    weight: float = Field(..., ge=0.0, le=1.0)


class DifficultyConfig(_StrictModel):
    """Configuration for difficulty distribution. Weights must sum to 1."""

    direct_identifier: float = Field(default=0.20, ge=0.0, le=1.0)
    alias: float = Field(default=0.25, ge=0.0, le=1.0)
    contextual: float = Field(default=0.25, ge=0.0, le=1.0)
    multimodal: float = Field(default=0.20, ge=0.0, le=1.0)
    cross_source: float = Field(default=0.10, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "DifficultyConfig":
        total = sum(self.model_dump().values())
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(f"difficulty weights must sum to 1.0, got {total:g}")
        return self


class DatasetConfig(_StrictModel):
    """Top-level dataset configuration."""

    config_schema_version: Literal["1.0"] = "1.0"
    tpcds_scale_factor: int = Field(default=1, ge=1)
    master_seed: int = Field(default=42)

    artifacts: Dict[str, ArtifactConfig] = Field(default_factory=dict)
    scenarios: Dict[str, ScenarioConfig] = Field(default_factory=dict)
    difficulty: DifficultyConfig = Field(default_factory=DifficultyConfig)

    @field_validator("artifacts")
    @classmethod
    def _known_artifact_types(cls, value: Dict[str, ArtifactConfig]) -> Dict[str, ArtifactConfig]:
        unknown = sorted(set(value) - set(ARTIFACT_TYPES))
        if unknown:
            raise ValueError(
                f"unknown artifact types {unknown}; expected any of {list(ARTIFACT_TYPES)}"
            )
        return value

    @field_validator("scenarios")
    @classmethod
    def _known_scenario_types(cls, value: Dict[str, ScenarioConfig]) -> Dict[str, ScenarioConfig]:
        unknown = sorted(set(value) - set(SCENARIO_TYPES))
        if unknown:
            raise ValueError(
                f"unknown scenario types {unknown}; expected any of {list(SCENARIO_TYPES)}"
            )
        if value and sum(s.weight for s in value.values()) <= 0:
            raise ValueError("at least one scenario must have a positive weight")
        return value

    def normalized_scenario_weights(self) -> Dict[str, float]:
        """Scenario weights scaled to sum to 1, in sorted key order."""
        total = sum(s.weight for s in self.scenarios.values())
        return {name: self.scenarios[name].weight / total for name in sorted(self.scenarios)}

    def canonical_json(self) -> str:
        """Canonical serialization: all fields (defaults included), sorted keys, no whitespace."""
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    def config_hash(self) -> str:
        """SHA-256 of ``canonical_json()``. Equal for equivalent configs."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


DIFFICULTY_PROFILES: Dict[str, Dict[str, object]] = {
    "easy": {
        "description": "Mostly direct identifiers and aliases",
        "weights": DifficultyConfig(
            direct_identifier=0.40, alias=0.35, contextual=0.15, multimodal=0.05, cross_source=0.05
        ),
    },
    "mixed": {
        "description": "Even mix of difficulty levels (default)",
        "weights": DifficultyConfig(),
    },
    "hard": {
        "description": "Mostly contextual and cross-source claims",
        "weights": DifficultyConfig(
            direct_identifier=0.05, alias=0.10, contextual=0.35, multimodal=0.30, cross_source=0.20
        ),
    },
}


def default_config() -> DatasetConfig:
    """Create default configuration for first release."""
    return DatasetConfig(
        tpcds_scale_factor=1,
        master_seed=42,
        artifacts={
            "pdf": ArtifactConfig(enabled=True, target_count=500),
            "image": ArtifactConfig(enabled=True, target_count=300),
            "email": ArtifactConfig(enabled=True, target_count=500),
            "chat": ArtifactConfig(enabled=True, target_count=300),
            "audio": ArtifactConfig(enabled=True, target_count=100),
            "video": ArtifactConfig(enabled=True, target_count=50),
        },
        scenarios={
            "product_return_damage": ScenarioConfig(weight=0.25),
            "warehouse_inventory_issue": ScenarioConfig(weight=0.20),
            "promotion_performance": ScenarioConfig(weight=0.15),
        },
        difficulty=DifficultyConfig(),
    )
