"""Configuration management endpoints."""
from typing import Dict

from fastapi import APIRouter
from pydantic import BaseModel

from helios_ds.config import DifficultyProfile, default_config

router = APIRouter()


class DifficultyOption(BaseModel):
    """Available difficulty profile option."""
    name: str
    description: str
    weights: Dict[str, float]


class ConfigTemplate(BaseModel):
    """Configuration template for UI form."""
    default_tpcds_scale_factors: list = [1, 10, 100]
    artifact_types: list = ["pdf", "email", "image", "chat", "audio", "video"]
    scenario_types: list = ["product_return_damage", "warehouse_inventory_issue", "promotion_performance"]
    difficulty_profiles: list


def _build_difficulty_profiles() -> list:
    """Build difficulty profile options."""
    profiles = [
        DifficultyOption(
            name="easy",
            description="Mostly direct identifiers and aliases",
            weights={
                "direct_identifier": 0.40,
                "alias": 0.35,
                "contextual": 0.15,
                "multimodal": 0.05,
                "cross_source": 0.05,
            },
        ),
        DifficultyOption(
            name="balanced",
            description="Even mix of difficulty levels (default)",
            weights={
                "direct_identifier": 0.20,
                "alias": 0.25,
                "contextual": 0.25,
                "multimodal": 0.20,
                "cross_source": 0.10,
            },
        ),
        DifficultyOption(
            name="hard",
            description="Mostly contextual and cross-source claims",
            weights={
                "direct_identifier": 0.05,
                "alias": 0.10,
                "contextual": 0.35,
                "multimodal": 0.30,
                "cross_source": 0.20,
            },
        ),
    ]
    return profiles


@router.get("/template", response_model=ConfigTemplate)
async def get_config_template() -> ConfigTemplate:
    """Get configuration template for UI form.

    Returns:
        ConfigTemplate with available options
    """
    return ConfigTemplate(
        difficulty_profiles=_build_difficulty_profiles(),
    )


@router.get("/default")
async def get_default_config():
    """Get default configuration.

    Returns:
        Default DatasetConfig
    """
    return default_config().model_dump()
