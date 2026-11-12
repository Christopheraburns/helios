"""The MCP server: ``build_server(stores, caller=...)`` makes it, ``serve_stdio(server)``
runs it on this process's stdin and stdout until the agent closes them.

Every tool in helios-serve's registry is offered with the registry's name, description
and input schema; ``readOnlyHint`` comes from its ``effect`` (no Helios tool changes
data, so it is set on all of them). A call's arguments are checked against the schema
before the tool runs; the tool's result is the call's structured content (and its JSON
text), and a result with an ``error`` is an error result the agent can read and correct.
Nothing here listens on a network.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from helios_cortex.caller import Caller
    from helios_serve import Registry, Stores, Tool
    from mcp.server import Server

LOGGER = logging.getLogger(__name__)

SERVER_NAME = "helios"
INSTRUCTIONS = (
    "Helios answers questions about data from published semantic models: list the models, search their "
    "datasets, fields and metrics, compile metrics and dimensions to SQL, and run it on the warehouse. "
    "Call search_semantics before compiling or running a query and copy names exactly from its results. "
    "This server is local and single-user: it acts with the credentials in this user's Helios settings.")


# The MCP hints for each registry effect. Neither changes data: ``read`` reads Helios's
# metadata and ``query`` runs SELECTs on the warehouse. An effect with no entry here (a
# future one that writes) fails at startup rather than being offered as read-only.
HINTS = {
    "read": {"read_only_hint": True, "destructive_hint": False, "idempotent_hint": True, "open_world_hint": False},
    "query": {"read_only_hint": True, "destructive_hint": False, "idempotent_hint": True, "open_world_hint": False},
}


def annotations_for(entry: Tool):
    """The MCP hints for a registry tool, from its ``effect``."""
    import mcp_types as types

    return types.ToolAnnotations(**HINTS[entry.effect.value])


def mcp_tool(entry: Tool):
    import mcp_types as types

    return types.Tool(name=entry.name, description=entry.description, input_schema=entry.input_schema,
                      annotations=annotations_for(entry))


def _result(payload: dict[str, Any]):
    import mcp_types as types

    payload = json.loads(json.dumps(payload, default=str))       # JSON as it stands, whatever a tool returned
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(payload))],
                                structured_content=payload, is_error=bool(payload.get("error")))


def _refused(code: str, message: str):
    return _result({"error": code, "message": message, "retryable": False})


def _argument_problem(entry: Tool, arguments: dict[str, Any]) -> str | None:
    from jsonschema import Draft202012Validator

    errors = sorted(Draft202012Validator(entry.input_schema).iter_errors(arguments), key=lambda e: list(e.path))
    if not errors:
        return None
    first = errors[0]
    where = ".".join(str(p) for p in first.path)
    return f"{where}: {first.message}" if where else first.message


def build_server(stores: Stores, *, caller: Caller | None = None, registry: Registry | None = None) -> Server:
    """The ``helios`` MCP server offering ``registry``'s tools (default: every installed
    one) on ``stores``, each call made as ``caller`` (None: the connections' own service
    identity, this user's credentials)."""
    import anyio
    import mcp_types as types
    from helios_serve import load_registry
    from mcp.server import Server

    import helios_mcp

    registry = registry if registry is not None else load_registry()
    listed = types.ListToolsResult(tools=[mcp_tool(entry) for entry in registry.tools()])

    async def list_tools(ctx, params):
        return listed

    async def call_tool(ctx, params: types.CallToolRequestParams):
        entry = registry.get(params.name)
        if entry is None:
            return _refused("unknown_tool", f"there is no tool named {params.name!r}; list the tools to see them")
        arguments = dict(params.arguments or {})
        problem = _argument_problem(entry, arguments)
        if problem:
            return _refused("invalid_tool_arguments", f"{params.name}: {problem}")
        try:
            result = await anyio.to_thread.run_sync(lambda: entry(caller, stores, **arguments))
        except Exception as exc:                 # the tool logged it; the agent gets no internals
            LOGGER.warning("tool %s raised %s", params.name, type(exc).__name__)
            return _result({"error": "tool_failed", "message": f"{params.name} failed; see the server's log",
                            "retryable": True})
        return _result(result if isinstance(result, dict) else {"result": result})

    return Server(SERVER_NAME, version=helios_mcp.__version__, title="Helios", instructions=INSTRUCTIONS,
                  on_list_tools=list_tools, on_call_tool=call_tool)


async def _serve(server: Server) -> None:
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def serve_stdio(server: Server) -> None:
    """Serve ``server`` on stdin and stdout until they close. Stdout carries the protocol,
    so anything else this process prints must go to stderr."""
    import anyio

    anyio.run(_serve, server)
