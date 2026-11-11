"""helios-cloudera's subcommand of ``helios``: ``impala``.

    helios --profile workbench impala diagnose [--delegated-user USER]
    helios --profile workbench impala init-index

``--profile workbench`` reads the Workbench variables (IMPALA_HOST, WORKLOAD_USER, ...);
without it the ``default`` connection comes from helios.toml or HELIOS_* variables.
"""
from __future__ import annotations

import argparse

from helios_cortex.cli import CommandContext, CommandError


class ImpalaCommand:
    help = "Impala on Cloudera: check the credentials, or create the helios_index tables"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        actions = parser.add_subparsers(dest="action", metavar="ACTION", required=True)
        diagnose = actions.add_parser("diagnose", help="test the workload credential, then an optional doAs user; "
                                                       "prints no credential or SQL")
        diagnose.add_argument("--delegated-user", metavar="USER",
                              help="an authenticated Cloudera SSO subject to verify through doAs")
        actions.add_parser("init-index", help="create the helios_index tables through Impala")

    def run(self, args: argparse.Namespace, context: CommandContext) -> int:
        if args.action == "diagnose":
            from helios_cloudera.impala_diagnose import DiagnosisFailed, diagnose

            try:
                diagnose(context.settings, args.delegated_user, context.echo)
            except DiagnosisFailed as exc:
                raise CommandError(str(exc)) from exc
            return 0
        from helios_cloudera.index import init_tables

        if not init_tables(context.settings):
            raise CommandError("Impala is not configured: no impala [connections.default] "
                               "(on Workbench, pass --profile workbench)")
        context.echo("helios_index tables are in place.")
        return 0
