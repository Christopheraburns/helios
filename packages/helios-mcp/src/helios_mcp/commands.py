"""``helios mcp``: serve the Helios tools over stdio to a local agent (helios.commands)."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from helios_cortex.cli import CommandError, add_caller_argument

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
        from helios_mcp.agents import AGENTS

        parser.add_argument("--print-config", choices=sorted(AGENTS), metavar="AGENT",
                            help="print the configuration that makes AGENT launch this server, with this "
                                 "environment's helios and config file as absolute paths, and exit: "
                                 + "; ".join(AGENTS.values()))

    def run(self, args: argparse.Namespace, context: CommandContext) -> int:
        if args.print_config:
            from helios_mcp.agents import launch, snippet

            command, launch_args = launch(_config_file(context), args.caller)
            context.echo(snippet(args.print_config, command, launch_args).rstrip("\n"))
            return 0
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


def _config_file(context: CommandContext) -> Path:
    """The config file this command reads (``--config``, ``HELIOS_CONFIG``, ``./helios.toml``,
    ``./pyproject.toml``), as an absolute path for the agent's configuration."""
    from helios_cortex.settings import config_file

    found = Path(context.config) if context.config else config_file()
    if found is None:
        raise CommandError("no helios.toml here: run `helios init`, or pass --config FILE")
    if not found.is_file():
        raise CommandError(f"Helios config file not found: {found}")
    if found.name == "pyproject.toml":
        import tomllib

        if "helios" not in tomllib.loads(found.read_text()).get("tool", {}):
            raise CommandError(f"{found} has no [tool.helios]: run `helios init`, or pass --config FILE")
    return found.resolve()
