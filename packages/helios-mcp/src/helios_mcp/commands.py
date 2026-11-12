"""``helios mcp``: serve the Helios tools over stdio to a local agent (helios.commands)."""
from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from helios_cortex.cli import add_caller_argument

if TYPE_CHECKING:
    from helios_cortex.cli import CommandContext


class McpCommand:
    help = "serve the Helios tools over MCP on stdio, for a local agent (single-user, unauthenticated)"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.description = (
            "Serve the Helios tools over MCP on stdin and stdout. An agent (Claude Code, Cursor, goose, "
            "Mistral Vibe) launches this command itself; you do not run it by hand. It is local, single-user "
            "and unauthenticated, acting with the credentials in your Helios settings; shared deployments "
            "serve the tools through Daedalus.")
        add_caller_argument(parser, "run every query")

    def run(self, args: argparse.Namespace, context: CommandContext) -> int:
        # Stdout is the protocol: nothing may be echoed. The helios command logs to stderr.
        from helios_serve import Stores, single_user_policy

        from helios_mcp.server import build_server, serve_stdio

        caller = context.caller(args.caller)
        # No caller: this user, with the connections' own (service) credentials, so the
        # single-user policy lets those run. A caller (--as, or HELIOS_CALLER_TOKEN for token
        # mode): the default policy, so the warehouse enforces that caller.
        overrides = {} if caller is not None else {"data_policy": single_user_policy()}
        serve_stdio(build_server(Stores.from_settings(context.settings, **overrides), caller=caller))
        return 0
