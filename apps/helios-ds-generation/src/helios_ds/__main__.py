"""Helios-DS command line.

  python -m helios_ds init-tables --lakehouse impala
  python -m helios_ds plan --tpcds impala:tpcds --lakehouse impala --store s3a://applied-ai-buk-d5eff1ab/helios-db/source
  python -m helios_ds plan --config fixtures/tiny/config.json --tpcds duckdb-generate:1 \\
      --lakehouse sql:sqlite:////tmp/c.db --warehouse file:///tmp/wh --store file:/tmp/objects
  python -m helios_ds approve <dataset_id> --approver user:alice --lakehouse ... --store ...
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from .backends import lakehouse_from_uri, object_store_from_uri, tpcds_from_uri
from .config import DatasetConfig, default_config
from .lifecycle import DatasetLifecycle
from .pipeline import plan_and_publish
from .templates import TemplateRegistry


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--lakehouse", help="impala or sql:<uri> (HELIOS_DS_LAKEHOUSE)")
    parser.add_argument("--warehouse", help="warehouse location for sql: (HELIOS_DS_WAREHOUSE)")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="helios_ds")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser(
        "init-tables", help="create helios_ds and helios_ground_truth tables"
    )
    _common(init)

    plan = commands.add_parser("plan", help="plan a dataset and publish its generation manifest")
    _common(plan)
    plan.add_argument("--config", help="DatasetConfig JSON file (default: built-in defaults)")
    plan.add_argument("--tpcds", help="TPC-DS source URI (HELIOS_DS_TPCDS)")
    plan.add_argument("--store", help="object store URI (HELIOS_DS_OBJECT_STORE)")
    plan.add_argument("--init-tables", action="store_true", help="create missing tables first")

    approve = commands.add_parser("approve", help="move an IN_REVIEW dataset to READY")
    _common(approve)
    approve.add_argument("dataset_id")
    approve.add_argument("--approver", required=True)
    approve.add_argument("--store", help="object store URI (HELIOS_DS_OBJECT_STORE)")

    args = parser.parse_args(argv)
    sink = lakehouse_from_uri(args.lakehouse, args.warehouse)

    if args.command == "init-tables":
        sink.ensure_tables()
        print("tables ready")
        return 0

    if args.command == "plan":
        if args.init_tables:
            sink.ensure_tables()
        config = (
            DatasetConfig.model_validate_json(Path(args.config).read_text())
            if args.config
            else default_config()
        )
        result = plan_and_publish(
            config,
            tpcds_from_uri(args.tpcds),
            TemplateRegistry.load(),
            sink,
            object_store_from_uri(args.store),
        )
        print(
            json.dumps(
                {
                    "dataset_id": result.dataset_id,
                    "run_id": result.run_id,
                    "state": result.state.value,
                    "newly_published": result.newly_published,
                    "manifest_sha256": result.manifest_sha256,
                    "artifact_counts": result.artifact_counts,
                    "scenario_counts": result.scenario_counts,
                },
                indent=2,
            )
        )
        return 0

    approval = DatasetLifecycle(sink).approve(
        args.dataset_id, object_store_from_uri(args.store), args.approver
    )
    print(f"{approval.record.dataset_id} -> {approval.record.state}")
    for other in approval.superseded:
        print(f"{other} -> SUPERSEDED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
