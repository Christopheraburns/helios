"""Request and response models for the /v1 REST contract (spec: "REST contract")."""

from typing import Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from helios_ds.config import (
    DIFFICULTY_PROFILES,
    ArtifactConfig,
    DatasetConfig,
    DifficultyConfig,
    ScenarioConfig,
    default_config,
)
from helios_ds.jobs import JobState, JobView


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
    # Scenario type -> relative weight (0 excludes it). Omit for the defaults.
    scenarios: Optional[Dict[str, float]] = None
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
        if self.scenarios is not None and not any(w > 0 for w in self.scenarios.values()):
            raise ValueError("select at least one scenario with a weight above 0")
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
            scenarios=(
                {name: ScenarioConfig(weight=w) for name, w in self.scenarios.items() if w > 0}
                if self.scenarios is not None
                else defaults.scenarios
            ),
            difficulty=weights,
        )

    @classmethod
    def default(cls) -> "GenerationRequest":
        defaults = default_config()
        return cls(
            artifact_counts={t: a.target_count for t, a in defaults.artifacts.items()},
            scenarios={name: s.weight for name, s in defaults.scenarios.items()},
        )


class GenerationAccepted(BaseModel):
    """202 response for POST /v1/generations."""

    job_id: str
    state: JobState
    status_uri: str


class Job(BaseModel):
    """GET /v1/jobs/{job_id}."""

    job_id: str
    state: JobState
    config_hash: str
    dataset_id: Optional[str] = None  # set when the worker has planned the dataset
    request: GenerationRequest
    created_at: str
    updated_at: str
    progress_percent: int = Field(default=0, ge=0, le=100)
    artifacts_generated: int = 0
    workbench_run_id: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None

    @classmethod
    def from_view(cls, view: JobView) -> "Job":
        return cls(
            job_id=view.job_id,
            state=view.state,
            config_hash=view.record.config_hash,
            dataset_id=view.dataset_id,
            request=GenerationRequest.model_validate(view.record.request),
            created_at=view.record.created_at,
            updated_at=view.updated_at,
            progress_percent=view.progress_percent,
            workbench_run_id=view.workbench_run_id,
            message=view.message,
            error=view.message if view.state is JobState.FAILED else None,
        )
