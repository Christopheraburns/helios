"""References to Helios-owned resources evaluated by authorization policy."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Resource:
    """Stable resource identity plus ownership context.

    ``model_id`` identifies the owning model for nested resources and for a
    model-scoped view of a referenced DataSource.
    """

    resource_type: str
    resource_id: str
    organization_id: str | None = None
    model_id: str | None = None

    def __post_init__(self) -> None:
        if not self.resource_type or not self.resource_type.strip():
            raise ValueError("resource_type must not be empty")
        if not self.resource_id or not self.resource_id.strip():
            raise ValueError("resource_id must not be empty")


# Compatibility name retained for existing core callers.
HeliosResource = Resource
