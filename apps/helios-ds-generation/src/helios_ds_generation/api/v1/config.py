"""Configuration options for the dashboard form. Not in the spec contract."""

from typing import Dict, List

from fastapi import APIRouter
from pydantic import BaseModel

from helios_ds.config import ARTIFACT_TYPES, DIFFICULTY_PROFILES, SCENARIO_TYPES, DifficultyConfig

from .models import GenerationRequest

router = APIRouter()


class DifficultyOption(BaseModel):
    name: str
    description: str
    weights: Dict[str, float]


class ConfigTemplate(BaseModel):
    scale_factors: List[int]
    artifact_types: List[str]
    scenario_types: List[str]
    difficulty_profiles: List[DifficultyOption]


@router.get("/config/template", response_model=ConfigTemplate)
async def get_config_template() -> ConfigTemplate:
    profiles = []
    for name, profile in DIFFICULTY_PROFILES.items():
        weights = profile["weights"]
        assert isinstance(weights, DifficultyConfig)
        profiles.append(
            DifficultyOption(
                name=name, description=str(profile["description"]), weights=weights.model_dump()
            )
        )
    return ConfigTemplate(
        scale_factors=[1, 10, 100],
        artifact_types=list(ARTIFACT_TYPES),
        scenario_types=list(SCENARIO_TYPES),
        difficulty_profiles=profiles,
    )


@router.get("/config/default", response_model=GenerationRequest)
async def get_default_request() -> GenerationRequest:
    """A default POST /v1/generations body."""
    return GenerationRequest.default()
