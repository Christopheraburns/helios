"""The MCP server: ``build_server()`` makes it, ``serve_stdio(server)`` runs it on this
process's stdin and stdout until the agent closes them.

The tools come from helios-serve's registry (7.2); this module owns the server's
identity and the stdio transport. Nothing here listens on a network.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server import Server

SERVER_NAME = "helios"
INSTRUCTIONS = (
    "Helios answers questions about data from published semantic models: list the models, search their "
    "datasets, fields and metrics, compile metrics and dimensions to SQL, and run it on the warehouse. "
    "Call search_semantics before compiling or running a query and copy names exactly from its results. "
    "This server is local and single-user: it acts with the credentials in this user's Helios settings.")


def build_server() -> Server:
    """The ``helios`` MCP server, named and versioned, with instructions for the agent."""
    from mcp.server import Server

    import helios_mcp

    return Server(SERVER_NAME, version=helios_mcp.__version__, title="Helios", instructions=INSTRUCTIONS)


async def _serve(server: Server) -> None:
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def serve_stdio(server: Server | None = None) -> None:
    """Serve ``server`` (default: ``build_server()``) on stdin and stdout until they close.
    Stdout carries the protocol, so anything else this process prints must go to stderr."""
    import anyio

    anyio.run(_serve, server if server is not None else build_server())
