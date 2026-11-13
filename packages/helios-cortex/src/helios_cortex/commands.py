"""helios-cortex's subcommands of ``helios``: ``init``, ``registry``, ``index``,
``source`` and ``model`` (registered under ``helios.commands`` in its pyproject)."""
from __future__ import annotations

import argparse
from pathlib import Path

from helios_cortex.cli import CommandContext, CommandError

STARTER_CONFIG = '''\
# Helios configuration. Every value can also be set as an environment variable:
# HELIOS_<NAME>, with sections joined by __ (HELIOS_CONNECTIONS__DEFAULT__PATH).
# Secrets are better kept in the environment than in this file. Relative file paths
# here are relative to this file, so the facade, `helios` commands and an agent's
# `helios mcp` read the same files wherever they start.

# Where Helios keeps its files (the SQLite registry, the model artifacts, runs).
# The default is your user data directory.
# data_dir = "helios-data"

# The registry of data sources, models and revisions: any SQLAlchemy URL. The default
# is a SQLite file in data_dir. A shared database needs `helios registry upgrade` once.
# registry_url = "postgresql+psycopg://helios@db.internal:5432/helios"

# The warehouse Helios harvests and queries. A data source names a connection
# (`helios source add warehouse --connection default`). Engines come from plugins:
# duckdb and sqlalchemy (`pip install 'helios-cortex[sqlalchemy]'`) and trino
# (`[trino]`) here, impala in helios-cloudera.
[connections.default]
engine = "duckdb"
path = "warehouse.duckdb"   # opened read-only; `read_only = false` to let Helios write it
# identity = "service"      # or "impersonate" / "token", if the engine supports it

# The LLM that proposes models (`pip install 'helios-cortex[llm]'`). The key is read
# from ANTHROPIC_API_KEY / MISTRAL_API_KEY, or set HELIOS_LLM__API_KEY.
# [llm]
# provider = "anthropic"    # or "mistral", or "openai" for an OpenAI-compatible endpoint
# model = "claude-sonnet-5-5"
# base_url = "https://inference.internal/v1"

# Model artifacts on object storage instead of data_dir (docs/storage.md).
# [artifact_storage]
# location = "s3://my-bucket/helios"
'''


class InitCommand:
    help = "write a starter helios.toml"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("path", nargs="?", type=Path, default=Path("helios.toml"),
                            help="where to write it (default: ./helios.toml)")
        parser.add_argument("--force", action="store_true", help="replace the file if it exists")

    def run(self, args: argparse.Namespace, context: CommandContext) -> int:
        path: Path = args.path
        if path.exists() and not args.force:
            raise CommandError(f"{path} already exists; pass --force to replace it")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(STARTER_CONFIG)
        context.echo(f"wrote {path}")
        return 0


class RegistryCommand:
    help = "the registry's schema: upgrade it, or show its version"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("action", choices=("upgrade", "current"),
                            help="upgrade: bring the schema to this helios-cortex's version; current: show it")

    def run(self, args: argparse.Namespace, context: CommandContext) -> int:
        from helios_cortex.registry.sql import SqlRegistry

        registry = SqlRegistry.from_settings(context.settings, upgrade=False)
        if args.action == "upgrade":
            registry.upgrade()
        context.echo(f"{registry!r}: schema {registry.current_revision() or 'empty'} "
                     f"(this helios-cortex: {registry.head_revision()})")
        return 0


class IndexCommand:
    help = "helios_index administration: print the Impala DDL"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("action", choices=("ddl",), help="ddl: print the Impala DDL (for Hue or impala-shell)")

    def run(self, args: argparse.Namespace, context: CommandContext) -> int:
        from helios_cortex.index.__main__ import impala_ddl

        context.out.write(impala_ddl())
        return 0


def _registry(context: CommandContext):
    from helios_cortex.registry.sql import SqlRegistry

    return SqlRegistry.from_settings(context.settings)


class SourceCommand:
    help = "register a data source (a connection Helios harvests), or list them"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        actions = parser.add_subparsers(dest="action", metavar="ACTION", required=True)
        add = actions.add_parser("add", help="register or replace a data source")
        add.add_argument("id", help="the data source's id, e.g. warehouse")
        add.add_argument("--connection", required=True, metavar="NAME",
                         help="the [connections.NAME] it uses")
        add.add_argument("--name", help="a display name (default: the id)")
        add.add_argument("--description", default="")
        actions.add_parser("list", help="list the registered data sources")

    def run(self, args: argparse.Namespace, context: CommandContext) -> int:
        from helios_cortex.domain import DataSource

        registry = _registry(context)
        if args.action == "list":
            for source in registry.data_sources():
                context.echo(f"{source.id}\t{source.name}\t{source.connector}\t{source.connection_ref}")
            return 0
        connection = context.settings.connections.get(args.connection)
        if connection is None:
            known = ", ".join(sorted(context.settings.connections)) or "none"
            raise CommandError(f"no connection {args.connection!r} in the settings (configured: {known})")
        registry.save_data_source(DataSource(args.id, args.name or args.id, connection.engine, args.connection,
                                             description=args.description))
        context.echo(f"data source {args.id}: connection {args.connection} ({connection.engine})")
        return 0


class ModelCommand:
    help = "register a model (what to build from which data sources), or list them"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        actions = parser.add_subparsers(dest="action", metavar="ACTION", required=True)
        add = actions.add_parser("add", help="register or replace a model")
        add.add_argument("id", help="the model's id, e.g. sales")
        add.add_argument("--name", required=True, help="its display name")
        add.add_argument("--source", required=True, action="append", metavar="ID",
                         help="a data source it is built from (repeat for more)")
        add.add_argument("--table", action="append", default=[], metavar="DB.TABLE",
                         help="limit it to these tables (repeat; only with one --source)")
        add.add_argument("--description", default="")
        add.add_argument("--as", dest="caller", metavar="USER", help="record USER as its creator")
        actions.add_parser("list", help="list the registered models")

    def run(self, args: argparse.Namespace, context: CommandContext) -> int:
        from helios_cortex.domain import DataSourceReference, Model

        registry = _registry(context)
        if args.action == "list":
            for registered in registry.models():
                context.echo(f"{registered.model.id}\t{registered.model.name}\t{registered.status}")
            return 0
        if args.table and len(args.source) > 1:
            raise CommandError("--table applies to one data source; register the model with one --source")
        missing = [s for s in args.source if registry.data_source(s) is None]
        if missing:
            raise CommandError(f"no data source {', '.join(missing)}; register it with `helios source add`")
        references = tuple(DataSourceReference(s, tuple(args.table)) for s in args.source)
        registry.save_model(Model(args.id, args.name, references, description=args.description),
                            created_by=args.caller)
        context.echo(f"model {args.id}: {', '.join(args.source)}")
        return 0
