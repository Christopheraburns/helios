"""The ``helios`` command.

    helios [--config FILE] [--profile NAME] [-v | -q] COMMAND ...

Subcommands come from the ``helios.commands`` entry-point group, so each installed
package adds its own: helios-cortex ``init``, ``registry``, ``index``, ``source`` and
``model``; helios-model ``harvest``, ``profile``, ``propose`` and ``publish``;
helios-cloudera ``impala``. A command is an object (or a class, instantiated with no
arguments) with:

    help: str                                            one line for ``helios --help``
    configure(parser: argparse.ArgumentParser) -> None   add its arguments
    run(args: argparse.Namespace, context: CommandContext) -> int   the exit status

A command writes its result to ``context.out`` and its progress through logging, which
this command, as the program, sends to stderr. It raises ``CommandError`` for anything
the user can fix; the message is shown without a traceback.
"""
from __future__ import annotations

import argparse
import contextlib
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TextIO

from helios_cortex import plugins
from helios_cortex.caller import Caller, IdentityError

if TYPE_CHECKING:
    from helios_cortex.settings import Settings

LOGGER = logging.getLogger(__name__)


class CommandError(Exception):
    """Something the user can fix: shown as ``helios: error: <message>``, exit status 1."""


class Command(Protocol):
    help: str

    def configure(self, parser: argparse.ArgumentParser) -> None: ...

    def run(self, args: argparse.Namespace, context: CommandContext) -> int: ...


class CommandContext:
    """What a command gets besides its arguments: the settings (built on first use, so
    ``helios init`` and ``--help`` read no configuration), the caller, and where its
    output goes."""

    def __init__(self, *, profile: str | None = None, config: Path | None = None, out: TextIO | None = None):
        self.profile = profile
        self.config = config
        self.out = out if out is not None else sys.stdout
        self._settings: Settings | None = None

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            from helios_cortex.settings import load_settings

            self._settings = load_settings(self.profile, self.config)
        return self._settings

    def caller(self, username: str | None) -> Caller | None:
        """The caller for ``--as USER``, with ``HELIOS_CALLER_TOKEN`` as its token when set;
        None (the connection's service identity) when neither is given."""
        from helios_cortex.settings import environment_caller_token

        token = environment_caller_token()
        if username is None and token is None:
            return None
        return Caller(username, token)

    def echo(self, text: str = "") -> None:
        print(text, file=self.out)


def add_caller_argument(parser: argparse.ArgumentParser, what: str) -> None:
    """``--as USER`` for a command that runs queries as a caller."""
    parser.add_argument("--as", dest="caller", metavar="USER",
                        help=f"{what} as USER (the connection's identity mode decides how); default: the "
                             "connection's service identity. A token is read from HELIOS_CALLER_TOKEN only.")


def _commands(stderr: TextIO) -> dict[str, Command]:
    commands: dict[str, Command] = {}
    for name in plugins.names(plugins.COMMANDS):
        try:
            command = plugins.load(plugins.COMMANDS, name)
        except plugins.PluginError as exc:          # one broken plugin does not take the others down
            print(f"helios: warning: {exc}", file=stderr)
            continue
        commands[name] = command() if isinstance(command, type) else command
    return commands


def _parser(commands: dict[str, Command]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="helios", description="Helios: build semantic models from your data and query them.",
        epilog="Commands come from the installed Helios packages; `helios COMMAND --help` describes one.")
    parser.add_argument("--config", type=Path, metavar="FILE",
                        help="the config file (default: HELIOS_CONFIG, else ./helios.toml, else pyproject.toml)")
    parser.add_argument("--profile", metavar="NAME",
                        help="a settings profile from an installed package, e.g. workbench (default: HELIOS_PROFILE)")
    loudness = parser.add_mutually_exclusive_group()
    loudness.add_argument("-v", "--verbose", action="store_true", help="show debug messages")
    loudness.add_argument("-q", "--quiet", action="store_true", help="show warnings and errors only")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    for name, command in commands.items():
        command.configure(sub.add_parser(name, help=command.help, description=command.help))
    return parser


def _unknown(argv: Sequence[str], commands: dict[str, Command]) -> str | None:
    """A hint when the first word is a command a package we know of would add."""
    first = next((a for a in argv if not a.startswith("-")), None)
    provider = plugins.PROVIDERS.get((plugins.COMMANDS, first)) if first else None
    if first and first not in commands and provider:
        return f"helios: error: {first!r} is a command from {provider}; install it"
    return None


def main(argv: Sequence[str] | None = None, *, out: TextIO | None = None, stderr: TextIO | None = None) -> int:
    """Run the command line ``argv`` (default: ``sys.argv[1:]``) and return its exit status."""
    argv = list(sys.argv[1:] if argv is None else argv)
    stderr = stderr if stderr is not None else sys.stderr
    commands = _commands(stderr)
    hint = _unknown(argv, commands)
    if hint:
        print(hint, file=stderr)
        return 2
    try:
        # argparse writes help and usage errors to sys.stdout / sys.stderr itself
        with contextlib.redirect_stdout(out or sys.stdout), contextlib.redirect_stderr(stderr):
            args = _parser(commands).parse_args(argv)
    except SystemExit as exc:                       # --help, or a usage error argparse reported
        return exc.code if isinstance(exc.code, int) else 2

    # The command line is a program: it decides where the libraries' log records go,
    # for as long as the command runs (main may be called in-process, by a Job or a test).
    level = logging.DEBUG if args.verbose else logging.WARNING if args.quiet else logging.INFO
    root = logging.getLogger()
    handler = logging.StreamHandler(stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.setLevel(level)
    chatty = {name: logging.getLogger(name) for name in ("alembic", "httpx", "httpcore")}  # unless asked for
    saved = (root.level, {name: logger.level for name, logger in chatty.items()})
    root.addHandler(handler)
    root.setLevel(min(level, root.level) if root.level else level)
    for logger in chatty.values():
        logger.setLevel(logging.DEBUG if args.verbose else logging.WARNING)

    context = CommandContext(profile=args.profile, config=args.config, out=out)
    try:
        return commands[args.command].run(args, context)
    except (CommandError, plugins.PluginError, IdentityError) as exc:
        print(f"helios: error: {exc}", file=stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        root.removeHandler(handler)
        root.setLevel(saved[0])
        for name, logger in chatty.items():
            logger.setLevel(saved[1][name])


if __name__ == "__main__":
    sys.exit(main())
