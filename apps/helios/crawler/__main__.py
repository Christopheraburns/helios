"""Run the crawler (a Workbench Job in the Helios project).

    python -m apps.helios.crawler crawl --dataset <dataset_id> [--full] [--s3-connection NAME]

Runs as the crawler identity (WORKLOAD_USER / WORKLOAD_PASSWORD for Impala) with
the active crawler settings and ontology version from helios_index.
"""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m apps.helios.crawler")
    commands = parser.add_subparsers(dest="command", required=True)
    crawl_cmd = commands.add_parser("crawl", help="crawl one Helios-DS dataset")
    crawl_cmd.add_argument("--dataset", required=True, help="Helios-DS dataset_id (must be READY)")
    crawl_cmd.add_argument("--full", action="store_true", help="re-fetch unchanged assets too")
    crawl_cmd.add_argument(
        "--s3-connection",
        default=os.environ.get("HELIOS_CRAWLER_S3_CONNECTION", "S3 Object Store"),
        help="Workbench data connection for artifact bytes",
    )
    args = parser.parse_args(argv)

    from helios_core.config import impala_config
    from helios_core.engines.impala import ImpalaEngine
    from helios_core.index import crawler_settings, impala_index_store, ontology_versions

    from .connector import HeliosDsConnector, object_reader, s3_client_from_connection
    from .crawl import crawl

    config = impala_config()
    index = impala_index_store()
    if config is None or index is None:
        print("Impala is not configured (IMPALA_HOST, WORKLOAD_USER, WORKLOAD_PASSWORD).")
        return 2
    index.ensure_tables()
    connection = ImpalaEngine(config).connect()
    connector = HeliosDsConnector(
        connection.cursor, object_reader(s3_client_from_connection(args.s3_connection))
    )
    settings_record, settings = crawler_settings.active(index)
    ontology = ontology_versions.active(index)
    run = crawl(
        index,
        connector,
        args.dataset,
        actor=config.user,
        ontology_version=ontology.version if ontology else "unpublished",
        settings=settings_record,
        settings_hash=settings.content_hash(),
        full=args.full,
    )
    print(f"{run.crawl_run_id}: {run.status} {run.counts}")
    return 0 if run.status == "SUCCEEDED" else 1


if __name__ == "__main__":
    sys.exit(main())
