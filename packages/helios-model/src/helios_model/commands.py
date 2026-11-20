"""helios-model's subcommands of ``helios``: ``harvest``, ``profile``, ``propose``,
``proposal show`` and ``publish``, over the facades in ``helios_model.discovery``.

    helios source add warehouse --connection default
    helios model add sales --name Sales --source warehouse
    helios harvest --source warehouse --database sales --model sales
    helios profile                                  # the latest run
    helios propose
    helios proposal show --out PROPOSAL.md          # every element, for review outside a UI
    helios publish --min-confidence 0.8             # accept what is that sure, then publish
    helios publish --review review.json             # or publish the decisions kept in a file

Each prints what it made on stdout (the run id first, so scripts can read it) and its
progress on stderr. A stage without ``--run`` works on the latest run.
"""
from __future__ import annotations

import argparse

from helios_cortex.cli import CommandContext, CommandError, add_caller_argument


def _run_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run", metavar="RUN_ID", help="the run to work on (default: the latest)")


def _run_id(args: argparse.Namespace, context: CommandContext) -> str:
    if args.run:
        return args.run
    from helios_cortex.runs import RunStore

    latest = RunStore.from_settings(context.settings).latest_run_id()
    if latest is None:
        raise CommandError("there are no runs yet; start one with `helios harvest`")
    return latest


def _read_review(path: str) -> dict:
    import json

    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        raise CommandError(f"cannot read the review {path}: {exc}") from exc


def _discovery_errors():
    from helios_model.discovery import DiscoveryError, ModelNotRegistered
    from helios_model.publication import PublicationValidationError

    return DiscoveryError, ModelNotRegistered, PublicationValidationError


class HarvestCommand:
    help = "harvest a data source's tables, columns, glossary and query log into a new run"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--source", required=True, metavar="ID", help="a registered data source")
        parser.add_argument("--database", required=True, action="append", metavar="DB",
                            help="a database (schema) to harvest; repeat for more")
        parser.add_argument("--model", required=True, metavar="ID", help="the model the run is for")
        parser.add_argument("--glossary", action="append", metavar="NAME",
                            help="only this catalog glossary (repeat; default: all)")
        parser.add_argument("--query-dir", metavar="DIR", help="a folder of .sql files to treat as query history")
        parser.add_argument("--catalog", metavar="NAME", help="the [catalogs.NAME] for the glossary (default: default)")
        parser.add_argument("--table", action="append", metavar="DB.TABLE",
                            help="harvest only this table (repeat; default: the model's selected tables, "
                                 "else every table)")
        parser.add_argument("--run-id", metavar="RUN_ID", help="the new run's id (default: the time, to the second)")
        add_caller_argument(parser, "harvest")

    def run(self, args: argparse.Namespace, context: CommandContext) -> int:
        from helios_cortex.registry import SqlRegistry
        from helios_model.discovery import Catalog

        source = SqlRegistry.from_settings(context.settings).data_source(args.source)
        if source is None:
            raise CommandError(f"no data source {args.source!r}; register it with `helios source add`")
        try:
            harvest = Catalog(context.settings).harvest(
                source, context.caller(args.caller), databases=args.database, model_id=args.model,
                glossaries=args.glossary, query_dir=args.query_dir, catalog=args.catalog, run_id=args.run_id,
                tables=args.table)
        except _discovery_errors() as exc:
            raise CommandError(str(exc)) from exc
        context.echo(harvest.run_id)
        context.echo(f"harvested {len(harvest.tables)} tables for model {harvest.model_id}")
        return 0


class ProfileCommand:
    help = "profile a run's harvest: column statistics, keys and verified relationships"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        _run_argument(parser)
        parser.add_argument("--overlap-threshold", type=float, default=0.95, metavar="RATIO",
                            help="the share of a key's values that must match to accept a relationship (default 0.95)")
        add_caller_argument(parser, "run the profiling queries")

    def run(self, args: argparse.Namespace, context: CommandContext) -> int:
        from helios_model.discovery import Profiler

        run_id = _run_id(args, context)
        try:
            profile = Profiler(context.settings, overlap_threshold=args.overlap_threshold).profile(
                run_id, context.caller(args.caller))
        except _discovery_errors() as exc:
            raise CommandError(str(exc)) from exc
        context.echo(run_id)
        context.echo(f"{len(profile.relationships)} relationships found, "
                     f"{len(profile.suggested_relationships)} suggested for the LLM to decide")
        return 0


class ProposeCommand:
    help = "propose a model from a run's harvest and profile, with the configured LLM"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        _run_argument(parser)
        parser.add_argument("--table", action="append", metavar="DB.TABLE",
                            help="propose only these harvested tables (repeat; default: all)")

    def run(self, args: argparse.Namespace, context: CommandContext) -> int:
        from helios_model.discovery import Proposer

        run_id = _run_id(args, context)
        try:
            proposals = Proposer(context.settings).propose(run_id, tables=args.table)
        except _discovery_errors() as exc:
            raise CommandError(str(exc)) from exc
        context.echo(run_id)
        context.echo(f"proposed {len(proposals.datasets)} datasets, {len(proposals.relationships)} relationships, "
                     f"{len(proposals.metrics)} metrics and {len(proposals.glossary_terms)} glossary terms "
                     "for review")
        return 0


class PublishCommand:
    help = "publish a run's reviewed proposals to the artifact store and the registry"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        _run_argument(parser)
        accept = parser.add_mutually_exclusive_group()
        accept.add_argument("--accept-pending", action="store_true",
                            help="accept every element still pending (not the LLM's rejections) before publishing")
        accept.add_argument("--min-confidence", type=float, metavar="C",
                            help="accept pending elements with at least this confidence (0-1) before publishing")
        parser.add_argument("--review", metavar="FILE",
                            help="a review.json kept elsewhere (for example in version control): its decisions "
                                 "replace the run's before publishing")
        parser.add_argument("--as", dest="caller", metavar="USER", help="record USER as the publisher (and reviewer)")

    def run(self, args: argparse.Namespace, context: CommandContext) -> int:
        from helios_model.discovery import Proposer, Publisher

        run_id = _run_id(args, context)
        caller = context.caller(args.caller)
        context.echo(run_id)
        try:
            proposer = Proposer(context.settings)
            if args.review:
                proposer.import_review(run_id, _read_review(args.review), caller)
                context.echo(f"decisions from {args.review}")
            proposals = proposer.load(run_id)
            if args.accept_pending or args.min_confidence is not None:
                accepted = proposals.accept_all(args.min_confidence or 0.0)
                proposer.save_review(proposals, caller)
                context.echo(f"accepted {accepted} pending elements")
            publication = Publisher(context.settings).publish(proposals, caller)
        except _discovery_errors() as exc:
            errors = getattr(exc, "errors", None)
            raise CommandError("; ".join(errors) if errors else str(exc)) from exc
        manifest = publication.manifest
        context.echo(f"published model {publication.model_id} revision {publication.revision_id}: "
                     f"{manifest['datasets']} datasets, {manifest['relationships']} relationships, "
                     f"{manifest['metrics']} metrics -> {manifest['path']}")
        return 0


class ProposalCommand:
    help = "show a run's proposal for review: every element with its confidence, decision and rationale"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        actions = parser.add_subparsers(dest="action", required=True)
        show = actions.add_parser("show", help="print the proposal (Markdown or JSON)")
        _run_argument(show)
        show.add_argument("--format", choices=("markdown", "json"), default="markdown")
        show.add_argument("--out", metavar="FILE", help="write it to FILE instead of stdout")

    def run(self, args: argparse.Namespace, context: CommandContext) -> int:
        import dataclasses
        import json

        from helios_model.discovery import Proposer

        run_id = _run_id(args, context)
        try:
            proposals = Proposer(context.settings).load(run_id)
        except _discovery_errors() as exc:
            raise CommandError(str(exc)) from exc
        if args.format == "markdown":
            text = proposals.to_markdown()
        else:
            text = json.dumps({"run_id": run_id, "model_id": proposals.model_id,
                               "elements": [{f.name: getattr(p, f.name) for f in dataclasses.fields(p)
                                             if f.name not in ("element", "overrides", "fields")}
                                            for p in proposals]}, indent=1, default=str)
        if args.out:
            with open(args.out, "w") as f:
                f.write(text + "\n")
            context.echo(f"wrote run {run_id}'s proposal to {args.out}")
        else:
            context.echo(text)
        return 0
