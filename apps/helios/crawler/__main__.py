"""Run the crawler (a Workbench Job in the Helios project).

    python -m apps.helios.crawler crawl --source <data_source_id> [--full]
    python -m apps.helios.crawler crawl --dataset <helios_ds_dataset_id> [--full]
    python -m apps.helios.crawler evaluate --run <crawl_run_id> --dataset <dataset_id>
                                           [--index-duckdb <path>]
    python -m apps.helios.crawler baseline record|check <file> [--run <crawl_run_id>]
                                           [--source <id>] [--dataset <dataset_id>]

``--source`` crawls a data source registered in Helios (Data Sources page), with
its connector, scope and crawl settings. ``--dataset`` is a shortcut for a
Helios-DS dataset that isn't registered (artifacts read through the
``--s3-connection`` data connection).

Runs as the crawler identity (WORKLOAD_USER / WORKLOAD_PASSWORD for Impala),
with the active ontology version and the crawler settings version the source
names (or the active one). The gazetteer is built from the warehouse through
the ontology mapping published for that ontology version, and joint resolution
queries the warehouse (read-only) through the same connection.

``--index-duckdb <path>`` writes the run tables (runs, assets, segments,
mentions, ...) to a local DuckDB file for development, while the ontology,
settings and TPC-DS are still read from Impala.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]


def source_snapshot(source: Any) -> dict[str, Any]:
    """A data source's configuration for the crawl record. Holds only the
    connection *reference*, never credentials."""
    return {
        "id": source.id,
        "organization_id": source.organization_id,
        "name": source.name,
        "connector": source.connector,
        "connection_ref": source.connection_ref,
        "scope": dict(source.scope),
        "crawl": dict(source.crawl),
    }


def crawl_request() -> dict[str, Any]:
    """Who asked for this crawl and the Workbench run doing it, when the API started it."""
    request = {
        "requested_by": os.environ.get("HELIOS_CRAWL_REQUESTED_BY"),
        "note": os.environ.get("HELIOS_CRAWL_NOTE", "").strip()[:200],
        "engine_id": os.environ.get("CDSW_ENGINE_ID")
        if os.environ.get("HELIOS_CRAWL_REQUESTED_BY")
        else None,
    }
    return {key: value for key, value in request.items() if value}


def embed_hook(development_index: bool, strategy: str = "deterministic") -> Any:
    """The step that makes a run's segments searchable, or None with the reason printed."""
    import importlib.util

    if strategy != "deterministic":
        print(f"embeddings: off (a {strategy} crawl is an experiment; it does not feed search)")
        return None
    if development_index:
        print("embeddings: off (a development index must not replace the searchable set)")
        return None
    if os.environ.get("HELIOS_CRAWL_EMBEDDINGS", "1") == "0":
        print("embeddings: off (HELIOS_CRAWL_EMBEDDINGS=0)")
        return None
    missing = [m for m in ("lancedb", "sentence_transformers") if importlib.util.find_spec(m) is None]
    if missing:
        print(f"embeddings: off ({', '.join(missing)} not installed)")
        return None
    from helios_core.index.evidence import embed_run, location

    print(f"embeddings: on ({location()})")
    return embed_run


def llm_extractor(settings: Any, classes: list[str]) -> Any:
    """The LLM crawler's extractor on the project's default model, or None with
    the reason printed."""
    from helios_core.llm import llm_from_env

    from .llm_arm import LlmExtractor, claim_shapes, load_vocabulary

    llm = llm_from_env()
    if llm is None:
        print("The llm strategy needs the project's LLM (LLM_PROVIDER and its key).")
        return None
    llm.timeout = max(llm.timeout, float(os.environ.get("HELIOS_CRAWL_LLM_TIMEOUT_SECONDS", "120")))
    vocabulary = load_vocabulary(REPO_ROOT / "ontology", classes, claim_shapes(settings))
    cache = os.environ.get("HELIOS_LLM_CACHE") or str(REPO_ROOT / "state" / "llm_cache")
    extractor = LlmExtractor(llm, settings, vocabulary, cache)
    print(
        f"llm: {llm.provider} {llm.model}, prompt {settings.llm.prompt_version} "
        f"({extractor.prompt_hash[:12]}), cache {cache}"
    )
    return extractor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m apps.helios.crawler")
    commands = parser.add_subparsers(dest="command", required=True)
    crawl_cmd = commands.add_parser("crawl", help="crawl one data source")
    which = crawl_cmd.add_mutually_exclusive_group(required=True)
    which.add_argument("--source", help="a data source ID registered in Helios")
    which.add_argument("--dataset", help="a READY Helios-DS dataset ID (unregistered)")
    crawl_cmd.add_argument("--full", action="store_true", help="re-fetch and re-analyze everything")
    crawl_cmd.add_argument(
        "--strategy",
        choices=["deterministic", "llm"],
        default="deterministic",
        help="how the text is understood: rules and the warehouse (default), or the project's LLM",
    )
    crawl_cmd.add_argument(
        "--s3-connection",
        default=os.environ.get("HELIOS_CRAWLER_S3_CONNECTION", "S3 Object Store"),
        help="Workbench data connection for --dataset artifacts",
    )
    crawl_cmd.add_argument(
        "--index-duckdb",
        metavar="PATH",
        help="write the run tables to this DuckDB file instead of the lakehouse (development)",
    )
    crawl_cmd.add_argument(
        "--classes",
        help="comma-separated ontology classes to look for (default: the mapping's scope)",
    )
    evaluate_cmd = commands.add_parser(
        "evaluate", help="score a crawl run against a ground-truth dataset (CR-8)"
    )
    evaluate_cmd.add_argument("--run", required=True, help="the crawl run ID to score")
    evaluate_cmd.add_argument("--dataset", required=True, help="the Helios-DS dataset ID")
    evaluate_cmd.add_argument(
        "--index-duckdb",
        metavar="PATH",
        help="read the run from (and write the scores to) this DuckDB index; truth from Impala",
    )
    baseline_cmd = commands.add_parser(
        "baseline",
        help="record what a crawl found, or check a crawl against a recorded baseline (CG-0)",
    )
    baseline_cmd.add_argument("action", choices=["record", "check"])
    baseline_cmd.add_argument("file", help="the baseline file (JSON)")
    baseline_cmd.add_argument("--run", help="the crawl run; default: the source's latest successful rules crawl")
    baseline_cmd.add_argument("--source", help="the source, when --run is not given (check: default from the file)")
    baseline_cmd.add_argument("--dataset", help="score against this ground-truth dataset too (check: default from the file)")
    args = parser.parse_args(argv)
    if args.command == "evaluate":
        return evaluate_main(args)
    if args.command == "baseline":
        return baseline_main(args)

    from helios_core.config import impala_config
    from helios_core.crawler.sources import is_crawlable
    from helios_core.domain import DataSource
    from helios_core.engines.impala import ImpalaEngine
    from helios_core.index import crawler_settings, impala_index_store, ontology_versions
    from helios_core.index.store import duckdb_index_store

    from .connector import s3_client_from_connection
    from .connectors import build
    from .crawl import crawl
    from .gazetteer import Gazetteer

    if args.source:
        from helios_core.metadata import SQLiteMetadataRepository

        source = SQLiteMetadataRepository().data_source(args.source)
        if source is None:
            print(f"No data source {args.source!r} in the Helios metadata store.")
            return 2
        if not is_crawlable(source.connector):
            print(
                f"Data source {source.name!r} uses connector {source.connector!r}, which isn't crawlable."
            )
            return 2
        if not source.crawl.get("enabled", True):
            print(f"Crawling is disabled for data source {source.name!r}.")
            return 2
    else:
        source = DataSource(
            id=args.dataset,
            organization_id="unregistered",
            name=f"Helios-DS dataset {args.dataset}",
            connector="helios_ds",
            connection_ref=args.s3_connection,
            scope={"dataset_id": args.dataset},
        )

    config = impala_config()
    catalog = impala_index_store()  # ontology versions and crawler settings
    if config is None or catalog is None:
        print("Impala is not configured (IMPALA_HOST, WORKLOAD_USER, WORKLOAD_PASSWORD).")
        return 2
    index = catalog  # the run tables
    if args.index_duckdb:
        index = duckdb_index_store(args.index_duckdb)
    index.ensure_tables()
    connection = ImpalaEngine(config).connect()
    connector = build(
        source.id,
        source.connector,
        source.connection_ref,
        dict(source.scope),
        cursor=connection.cursor,
        s3_client_for=s3_client_from_connection,
    )
    wanted = source.crawl.get("settings_version")
    if wanted:
        settings_record = crawler_settings.get(catalog, int(wanted))
        settings = crawler_settings.settings_of(settings_record)
    else:
        settings_record, settings = crawler_settings.active(catalog)
    if settings.is_empty():
        print(
            "warning: no crawler settings are active, and the engine has no rules of its own "
            "(no patterns, labels, phrases or claim cues). Load a preset on the Crawler page's "
            "Settings tab, save it and activate it."
        )
    ontology = ontology_versions.active(catalog)
    ontology_version = ontology.version if ontology else "unpublished"

    resolution = resolution_for(ontology_version, catalog)
    gazetteer = None
    if resolution is not None:
        if args.classes:
            classes = [c.strip() for c in args.classes.split(",") if c.strip()]
        else:
            classes = resolution.candidate_classes(source_uri(source, connector))
        gazetteer = Gazetteer.build(connection.cursor, resolution, classes, settings.dictionary)
        print(
            f"gazetteer: {len(gazetteer.forms)} forms for {', '.join(gazetteer.classes)} "
            f"(mapping {resolution.model} for ontology {resolution.ontology_version})"
        )
    extractor = None
    if args.strategy == "llm":
        extractor = llm_extractor(settings, classes) if gazetteer is not None else None
        if extractor is None:
            if gazetteer is None:
                print("The llm strategy needs an ontology mapping for the active ontology version.")
            return 2
    run = crawl(
        index,
        connector,
        source.id,
        actor=config.user,
        ontology_version=ontology_version,
        settings=settings_record,
        settings_hash=settings.content_hash(),
        crawler_settings=settings,
        full=args.full,
        source_snapshot=source_snapshot(source),
        request=crawl_request(),
        gazetteer=gazetteer,
        resolution=resolution,
        warehouse_cursor=connection.cursor,
        embed=embed_hook(bool(args.index_duckdb), args.strategy),
        strategy=args.strategy,
        llm=extractor,
    )
    print(f"{run.crawl_run_id}: {run.status} {run.counts}")
    return 0 if run.status == "SUCCEEDED" else 1


def resolution_for(ontology_version: str, catalog: Any) -> Any:
    """The resolver's view of the active mapping for ``ontology_version``, from
    ``helios_index`` (CG-6). Falls back to the only active mapping there is.
    The repository's mapping files are not read here: they are shipped
    examples, imported once through the API."""
    from helios_core.index import mappings as stored
    from helios_core.ontology.mapping import resolution_config

    active = stored.active(catalog)
    if not active:
        print(
            "warning: no mapping is active, so mentions are not extracted. Import or save one "
            "and activate it: POST /api/v1/ontology/mappings, then /mappings/{version}:activate."
        )
        return None
    records = list(active.values())
    matching = [
        r
        for r in records
        if r.ontology_version == ontology_version or r.ontology_version.endswith(f"@{ontology_version}")
    ]
    if not matching and len(records) == 1:
        matching = records
        print(
            f"warning: no mapping for ontology {ontology_version}; using {records[0].model} "
            f"(for {records[0].ontology_version})"
        )
    if not matching:
        print(f"warning: no mapping for ontology {ontology_version}; mentions are not extracted")
        return None
    chosen = matching[0]
    print(f"mapping: {chosen.model} version {chosen.version} ({chosen.content_hash[:12]})")
    return resolution_config(stored.mapping_of(chosen))


def source_uri(source: Any, connector: Any) -> str:
    """Where the source's assets live, for the mapping's resolution scope: the
    first asset's S3 location when the connector reads S3, else the scope's
    bucket and prefix, else the connector type."""
    scope = dict(source.scope)
    if "bucket" in scope:
        return f"s3a://{scope['bucket']}/{scope.get('prefix', '')}"
    try:
        for asset in connector.list_assets():
            locator = asset.locator
            if "bucket" in locator and "key" in locator:
                return f"s3a://{locator['bucket']}/{locator['key']}"
            break
    except Exception as exc:  # noqa: BLE001 - the crawl itself reports listing errors
        print(f"warning: could not list {source.name!r} to pick candidate classes: {exc}")
    return f"{source.connector}://{source.id}"


def evaluate_main(args: Any) -> int:
    """``evaluate``: score a crawl run as the session's Impala user (which must be
    allowed to read helios_ground_truth; the crawler identity never is)."""
    from helios_core.config import impala_config
    from helios_core.engines.impala import ImpalaEngine
    from helios_core.index import impala_index_store
    from helios_core.index.store import duckdb_index_store

    from . import evaluate as harness

    config = impala_config()
    index = duckdb_index_store(args.index_duckdb) if args.index_duckdb else impala_index_store()
    if config is None or index is None:
        print("Impala is not configured (IMPALA_HOST, WORKLOAD_USER, WORKLOAD_PASSWORD).")
        return 2
    index.ensure_tables()
    truth = ImpalaEngine(config).connect()
    try:
        record = harness.evaluate(
            index,
            truth.cursor,
            args.run,
            args.dataset,
            evaluator=config.user,
            evaluator_mode="workload_user",
        )
    except harness.UnknownRun:
        print(f"No crawl run {args.run!r} in the index.")
        return 2
    except harness.GroundTruthDenied as exc:
        print(f"Ground truth is not readable by {config.user}: {exc}")
        return 3
    except harness.EvaluationFailed as exc:
        print(harness.summary_table(exc.record))
        return 1
    finally:
        truth.close()
    print(harness.summary_table(record))
    return 0


def baseline_main(args: Any) -> int:
    """``baseline``: 0 if recorded or the same, 1 if the crawl differs, 2 if it could not run."""
    import json

    from helios_core.config import impala_config
    from helios_core.engines.impala import ImpalaEngine
    from helios_core.index import impala_index_store, runs

    from . import baseline

    config, index = impala_config(), impala_index_store()
    if config is None or index is None:
        print("Impala is not configured (IMPALA_HOST, WORKLOAD_USER, WORKLOAD_PASSWORD).")
        return 2
    path = Path(args.file)
    recorded = json.loads(path.read_text()) if args.action == "check" else {}
    source = args.source or recorded.get("source")
    dataset = args.dataset or recorded.get("dataset_id")
    strategy = recorded.get("strategy", "deterministic")
    candidates = [
        r
        for r in runs.runs(index)
        if r.status == "SUCCEEDED"
        and (r.crawl_run_id == args.run if args.run else r.source == source)
        and (args.run or str((r.settings or {}).get("strategy") or "deterministic") == strategy)
    ]
    if not candidates:
        print("No successful crawl run matches (give --run, or --source).")
        return 2
    run = candidates[0]
    truth = ImpalaEngine(config).connect() if dataset else None
    try:
        current = baseline.fingerprint(
            index, run, truth_cursor=truth.cursor if truth else None, dataset_id=dataset
        )
    finally:
        if truth is not None:
            truth.close()
    if args.action == "record":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        print(f"recorded {run.crawl_run_id} ({run.crawler_version}) to {path}")
        return 0
    differences = baseline.compare(recorded, current)
    print(
        f"{run.crawl_run_id} ({run.crawler_version}) against the baseline from "
        f"{recorded['recorded_from']['crawl_run_id']} ({recorded['recorded_from']['crawler_version']})"
    )
    for line in differences:
        print(f"  DIFFERENT  {line}")
    if not differences:
        tables = ", ".join(f"{t['rows']} {name}" for name, t in current["tables"].items())
        print(f"  SAME  {tables}" + ("; scorecard identical" if "scorecard" in current else ""))
    return 1 if differences else 0


if __name__ == "__main__":
    sys.exit(main())
