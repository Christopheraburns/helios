"""Helios model discovery: harvest a data source's tables, profile them, propose a
semantic model and publish it (burndown Phase 5).

The facades are ``Catalog``, ``Profiler``, ``Proposer`` and ``Publisher``
(``helios_model.discovery``), with the typed ``Proposals`` they pass along.

helios-model depends on helios-cortex alone; engines and catalogs come from plugins
(helios_cortex.plugins), so it runs on any warehouse an installed plugin supports.
"""

_EXPORTS = {
    "Catalog": "discovery", "Profiler": "discovery", "Proposer": "discovery", "Publisher": "discovery",
    "Harvest": "discovery", "Profile": "discovery", "Publication": "discovery",
    "DiscoveryError": "discovery", "ModelNotRegistered": "discovery",
    "Proposals": "proposals", "Proposal": "proposals", "ProposedDataset": "proposals",
    "ProposedField": "proposals", "ProposedRelationship": "proposals", "ProposedMetric": "proposals",
    "ProposedTerm": "proposals",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    # Exports are imported on first use, so importing helios_model loads no engine,
    # store or LLM code; __version__ is read from the installed metadata.
    if name in _EXPORTS:
        from importlib import import_module

        return getattr(import_module(f"helios_model.{_EXPORTS[name]}"), name)
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("helios-model")
        except PackageNotFoundError:  # running from a source path without an install
            return "0.0.0+unknown"
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
