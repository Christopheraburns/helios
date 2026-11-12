"""Helios tools: what an agent or an application asks of published semantic models
(burndown Phase 6).

The seven structured tools (list and describe models, search their semantics, compile
and run queries, explain lineage) are plain functions ``(caller, stores, **arguments)
-> dict`` with a registry of their names, descriptions and input schemas. helios-serve
has no MCP dependency and no HTTP server: Daedalus serves the tools to shared
deployments and helios-mcp to one developer's local agent.

helios-serve depends on helios-cortex alone; engines and catalogs come from plugins.
"""


def __getattr__(name: str) -> str:
    # __version__ is read from the installed metadata on first use, so importing
    # helios_serve opens no files.
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("helios-serve")
        except PackageNotFoundError:  # running from a source path without an install
            return "0.0.0+unknown"
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
