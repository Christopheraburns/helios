"""Request and response models for the /v1 REST contract (spec: "REST contract")."""

from enum import Enum
from typing import Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from helios_ds.config import (
    DIFFICULTY_PROFILES,
    ArtifactConfig,
    DatasetConfig,
    DifficultyConfig,
    default_config,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceSpec(_StrictModel):
    """Where the structured TPC-DS world is read from."""

    catalog: str = "hive"
    database: str = "tpcds"
    scale_factor: int = Field(default=1, ge=1)


class GenerationRequest(_StrictModel):
    """Body of POST /v1/generations."""

    source: SourceSpec = Field(default_factory=SourceSpec)
    master_seed: int = 42
    profile: str = "developer"
    artifact_counts: Dict[str, int] = Field(default_factory=dict)
    security_profile: str = "departmental"
    difficulty_profile: str = "mixed"

    def to_dataset_config(self) -> DatasetConfig:
        """Validate against the generator's DatasetConfig (raises ValueError on bad input)."""
        profile = DIFFICULTY_PROFILES.get(self.difficulty_profile)
        if profile is None:
            raise ValueError(
                f"unknown difficulty_profile '{self.difficulty_profile}'; "
                f"expected one of {sorted(DIFFICULTY_PROFILES)}"
            )
        weights = profile["weights"]
        assert isinstance(weights, DifficultyConfig)
        defaults = default_config()
        artifacts = (
            {
                t: ArtifactConfig(enabled=n > 0, target_count=n)
                for t, n in self.artifact_counts.items()
            }
            if self.artifact_counts
            else defaults.artifacts
        )
        return DatasetConfig(
            tpcds_scale_factor=self.source.scale_factor,
            master_seed=self.master_seed,
            artifacts=artifacts,
            scenarios=defaults.scenarios,
            difficulty=weights,
        )

    @classmethod
    def default(cls) -> "GenerationRequest":
        defaults = default_config()
        return cls(artifact_counts={t: a.target_count for t, a in defaults.artifacts.items()})


class JobState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


CANCELLABLE_STATES = {JobState.QUEUED, JobState.RUNNING}


class GenerationAccepted(BaseModel):
    """202 response for POST /v1/generations."""

    job_id: str
    state: JobState
    status_uri: str


class Job(BaseModel):
    """GET /v1/jobs/{job_id}."""

    job_id: str
    state: JobState
    dataset_id: str
    request: GenerationRequest
    created_at: str
    updated_at: str
    progress_percent: int = Field(default=0, ge=0, le=100)
    artifacts_generated: int = 0
    error: Optional[str] = None
