"""``helios mcp``: serve the Helios tools over stdio to a local agent (helios.commands)."""
from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

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

    def run(self, args: argparse.Namespace, context: CommandContext) -> int:
        # Stdout is the protocol: nothing may be echoed. The helios command logs to stderr.
        from helios_mcp.server import serve_stdio

        serve_stdio()
        return 0
