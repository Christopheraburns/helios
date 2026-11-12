"""helios-mcp: the Helios tools over MCP on stdio, for one developer's local agent
(burndown Phase 7).

``helios mcp`` starts it; an agent launches that command and talks to it over its stdin
and stdout. It is local, single-user and unauthenticated: it acts with the credentials in
your Helios settings, and serves nothing over the network. Shared, governed deployments
serve the same tools through Daedalus.
"""

_EXPORTS = {"build_server": "server", "serve_stdio": "server", "SERVER_NAME": "server"}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    # Exports are imported on first use, so importing helios_mcp loads neither the MCP SDK nor any tool.
    if name in _EXPORTS:
        from importlib import import_module

        return getattr(import_module(f"helios_mcp.{_EXPORTS[name]}"), name)
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("helios-mcp")
        except PackageNotFoundError:  # running from a source path without an install
            return "0.0.0+unknown"
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
