"""The console's names for runs that predate registered models (burndown 5.7).

helios-model no longer guesses: a published model is named by its id and a run must
name its model. The console keeps its old behaviour until Phase 8 deletes it: the Ossie
name is the first harvested database, and a run with no model id belongs to that name.
"""
from __future__ import annotations

from typing import Any

from helios_cortex.artifacts import model_id_for_run


def model_name_for_proposal(proposal: dict[str, Any], harvest: dict[str, Any]) -> str:
    databases = harvest.get("databases") or []
    if databases:
        return str(databases[0])
    datasets = proposal.get("datasets") or []
    if datasets:
        return str(datasets[0]["table"]).split(".")[0]
    return "helios"


def legacy_model_id(*documents: dict[str, Any], fallback: str) -> str:
    """The run's model id, or ``fallback`` when no document names one."""
    if any(document.get("model_id") for document in documents):
        return model_id_for_run(*documents)
    return model_id_for_run(explicit=fallback)
