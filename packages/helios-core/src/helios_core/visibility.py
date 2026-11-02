"""Which models a caller may list and use: decided by the host, not by Helios.

A host supplies a ModelVisibility (from its own groups, tenants, licences, ...).
The default, AllModels, shows every model in the registry. A model the caller
may not see is reported exactly like one that does not exist, so its name is
not disclosed.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from helios_core.caller import Caller


class ModelVisibility(Protocol):
    """True when ``caller`` may list and use the model ``model_id``."""

    def can_see(self, caller: Caller, model_id: str) -> bool: ...


class AllModels:
    """Every model in the registry is visible to every caller."""

    def can_see(self, caller: Caller, model_id: str) -> bool:
        return True


class ModelNotVisible(LookupError):
    """The model does not exist, or the caller may not see it; deliberately the same."""

    def __init__(self, model_id: str):
        self.model_id = model_id
        super().__init__(f"model {model_id!r} not found")


def visible_models(visibility: ModelVisibility, caller: Caller, model_ids: Iterable[str]) -> list[str]:
    """``model_ids`` that ``caller`` may see, in their original order."""
    return [model_id for model_id in model_ids if visibility.can_see(caller, model_id)]


def require_visible(visibility: ModelVisibility, caller: Caller, model_id: str) -> str:
    """Return ``model_id``, or raise ModelNotVisible when the caller may not see it."""
    if not visibility.can_see(caller, model_id):
        raise ModelNotVisible(model_id)
    return model_id
