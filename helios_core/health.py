"""Transport-neutral health checks with safe, non-secret results."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable, Literal

from helios_core.atlas import AtlasClient
from helios_core.config import atlas_config, impala_config
from helios_core.engines import ImpalaEngine
from helios_core.llm import llm_from_env
from helios_core.metadata import MetadataRepository

HealthState = Literal["healthy", "degraded", "unavailable", "unknown"]


@dataclass(frozen=True)
class HealthComponent:
    id: str
    label: str
    status: HealthState
    description: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def aggregate_status(components: list[HealthComponent]) -> HealthState:
    states = {component.status for component in components}
    if not states or states == {"unknown"}:
        return "unknown"
    if states == {"healthy"}:
        return "healthy"
    if states == {"unavailable"}:
        return "unavailable"
    return "degraded"


def _probe(
    component_id: str,
    label: str,
    check: Callable[[], bool],
    healthy_description: str,
    failure_description: str,
) -> HealthComponent:
    try:
        if check():
            return HealthComponent(
                component_id,
                label,
                "healthy",
                healthy_description,
            )
    except Exception:
        pass
    return HealthComponent(
        component_id,
        label,
        "unavailable",
        failure_description,
    )


def collect_infrastructure_status(
    metadata: MetadataRepository,
) -> list[HealthComponent]:
    """Run independent checks so one failed dependency does not hide the rest."""
    components: list[HealthComponent] = []
    try:
        version = metadata.schema_version()
        components.append(
            HealthComponent(
                "metadata",
                "Metadata repository",
                "healthy" if version > 0 else "degraded",
                (
                    "Metadata repository is available."
                    if version > 0
                    else "Metadata repository has no applied schema."
                ),
            )
        )
    except Exception:
        components.append(
            HealthComponent(
                "metadata",
                "Metadata repository",
                "unavailable",
                "Metadata repository check failed.",
            )
        )

    atlas = atlas_config()
    components.append(
        _probe(
            "atlas",
            "Atlas",
            lambda: AtlasClient(atlas).ping(),
            "Atlas connectivity check succeeded.",
            "Atlas connectivity check failed.",
        )
        if atlas
        else HealthComponent(
            "atlas",
            "Atlas",
            "unknown",
            "Atlas is not configured.",
        )
    )

    impala = impala_config()
    components.append(
        _probe(
            "impala",
            "Impala data source",
            lambda: ImpalaEngine(impala).ping(),
            "Impala connectivity check succeeded.",
            "Impala connectivity check failed.",
        )
        if impala
        else HealthComponent(
            "impala",
            "Impala data source",
            "unknown",
            "Impala is not configured.",
        )
    )

    llm = llm_from_env()
    components.append(
        _probe(
            "discovery-proposals",
            "Discovery proposal service",
            llm.ping,
            "Discovery proposal service responded.",
            "Discovery proposal service check failed.",
        )
        if llm
        else HealthComponent(
            "discovery-proposals",
            "Discovery proposal service",
            "unknown",
            "No proposal-generation service is configured.",
        )
    )
    return components
