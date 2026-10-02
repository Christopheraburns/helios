"""Run the crawler (a Workbench Job in the Helios project).

    python -m apps.helios.crawler crawl --source <data_source_id> [--full]
    python -m apps.helios.crawler crawl --dataset <helios_ds_dataset_id> [--full]

``--source`` crawls a data source registered in Helios (Data Sources page), with
its connector, scope and crawl settings. ``--dataset`` is a shortcut for a
Helios-DS dataset that isn't registered (artifacts read through the
``--s3-connection`` data connection).

Runs as the crawler identity (WORKLOAD_USER / WORKLOAD_PASSWORD for Impala),
with the active ontology version and the crawler settings version the source
names (or the active one).
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m apps.helios.crawler")
    commands = parser.add_subparsers(dest="command", required=True)
    crawl_cmd = commands.add_parser("crawl", help="crawl one data source")
    which = crawl_cmd.add_mutually_exclusive_group(required=True)
    which.add_argument("--source", help="a data source ID registered in Helios")
    which.add_argument("--dataset", help="a READY Helios-DS dataset ID (unregistered)")
    crawl_cmd.add_argument("--full", action="store_true", help="re-fetch and re-analyze everything")
    crawl_cmd.add_argument(
        "--s3-connection",
        default=os.environ.get("HELIOS_CRAWLER_S3_CONNECTION", "S3 Object Store"),
        help="Workbench data connection for --dataset artifacts",
    )
    args = parser.parse_args(argv)

    from helios_core.config import impala_config
    from helios_core.crawler.sources import is_crawlable
    from helios_core.domain import DataSource
    from helios_core.engines.impala import ImpalaEngine
    from helios_core.index import crawler_settings, impala_index_store, ontology_versions

    from .connector import s3_client_from_connection
    from .connectors import build
    from .crawl import crawl

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
    index = impala_index_store()
    if config is None or index is None:
        print("Impala is not configured (IMPALA_HOST, WORKLOAD_USER, WORKLOAD_PASSWORD).")
        return 2
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
        settings_record = crawler_settings.get(index, int(wanted))
        settings = crawler_settings.settings_of(settings_record)
    else:
        settings_record, settings = crawler_settings.active(index)
    ontology = ontology_versions.active(index)
    run = crawl(
        index,
        connector,
        source.id,
        actor=config.user,
        ontology_version=ontology.version if ontology else "unpublished",
        settings=settings_record,
        settings_hash=settings.content_hash(),
        crawler_settings=settings,
        full=args.full,
        source_snapshot=source_snapshot(source),
    )
    print(f"{run.crawl_run_id}: {run.status} {run.counts}")
    return 0 if run.status == "SUCCEEDED" else 1


if __name__ == "__main__":
    sys.exit(main())
