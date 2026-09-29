"""Configuration schema for Helios-DS generation."""
from enum import Enum
from typing import Dict, Optional

from pydantic import BaseModel, Field


class DifficultyProfile(str, Enum):
    """Difficulty tiers for generated artifacts."""
    DIRECT_IDENTIFIER = "direct_identifier"
    ALIAS = "alias"
    CONTEXTUAL = "contextual"
    MULTIMODAL = "multimodal"
    CROSS_SOURCE = "cross_source"


class ArtifactConfig(BaseModel):
    """Configuration for a single artifact type."""
    enabled: bool = True
    target_count: int = Field(..., ge=1)


class ScenarioConfig(BaseModel):
    """Configuration for a scenario type."""
    weight: float = Field(..., ge=0.0, le=1.0)


class DifficultyConfig(BaseModel):
    """Configuration for difficulty distribution."""
    direct_identifier: float = Field(default=0.20, ge=0.0, le=1.0)
    alias: float = Field(default=0.25, ge=0.0, le=1.0)
    contextual: float = Field(default=0.25, ge=0.0, le=1.0)
    multimodal: float = Field(default=0.20, ge=0.0, le=1.0)
    cross_source: float = Field(default=0.10, ge=0.0, le=1.0)

    def validate_weights_sum_to_one(self) -> None:
        """Ensure difficulty weights sum to approximately 1.0."""
        total = sum([
            self.direct_identifier,
            self.alias,
            self.contextual,
            self.multimodal,
            self.cross_source
        ])
        if not (0.99 <= total <= 1.01):  # Allow small floating-point error
            raise ValueError(
                f"Difficulty weights must sum to 1.0, got {total}"
            )


class DatasetConfig(BaseModel):
    """Top-level dataset configuration."""
    tpcds_scale_factor: int = Field(default=1, ge=1)
    master_seed: int = Field(default=42)

    artifacts: Dict[str, ArtifactConfig] = Field(default_factory=dict)
    scenarios: Dict[str, ScenarioConfig] = Field(default_factory=dict)
    difficulty: DifficultyConfig = Field(default_factory=DifficultyConfig)

    class Config:
        """Pydantic config."""
        use_enum_values = False


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
