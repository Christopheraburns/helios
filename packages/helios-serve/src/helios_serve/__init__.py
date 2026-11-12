"""Helios tools: what an agent or an application asks of published semantic models
(burndown Phase 6).

The seven structured tools (list and describe models, search their semantics, compile
and run queries, explain lineage) are plain functions ``(caller, stores, **arguments)
-> dict`` with a registry of their names, descriptions and input schemas. helios-serve
has no MCP dependency and no HTTP server: Daedalus serves the tools to shared
deployments and helios-mcp to one developer's local agent.

helios-serve depends on helios-cortex alone; engines and catalogs come from plugins.
"""


_EXPORTS = {
    "Stores": "stores", "ModelStore": "stores", "ModelNotAvailable": "stores", "model_connection": "stores",
    **{name: "tools" for name in ("list_models", "describe_model", "search_semantics", "describe",
                                  "compile_query", "run_query", "explain_lineage", "TOOLS")},
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    # Exports are imported on first use, so importing helios_serve loads no engine or
    # store code; __version__ is read from the installed metadata.
    if name in _EXPORTS:
        from importlib import import_module

        return getattr(import_module(f"helios_serve.{_EXPORTS[name]}"), name)
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("helios-serve")
        except PackageNotFoundError:  # running from a source path without an install
            return "0.0.0+unknown"
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
