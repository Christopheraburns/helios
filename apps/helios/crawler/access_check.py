"""CR-0d: check that the crawler's identity sees exactly what it should.

Run in the Helios project, with the crawler's credentials, after the Ranger and
RAZ policies are in place:

    python -m apps.helios.crawler.access_check [--connection "S3 Object Store"]

Must be allowed: SELECT on helios_ds.crawlable_artifacts and on the warehouse the
mapping names (a table of the active mapping, or ``--warehouse-table``), creating
and writing tables in helios_index, and reading artifact objects under
helios-db/source/datasets/. Must be denied: helios_ground_truth.*,
the helios_ds base tables, and generation manifests (_manifests/), which hold
story facts. Exits non-zero if anything is wrong.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

CRAWLABLE = "helios_ds.crawlable_artifacts"
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MUST_DENY_TABLES = (
    "helios_ground_truth.claims",
    "helios_ground_truth.entity_mentions",
    "helios_ground_truth.expected_queries",
    "helios_ds.artifacts",
    "helios_ds.scenario_plans",
)


@dataclass
class Check:
    name: str
    expected: str  # "allowed" or "denied"
    outcome: str  # "allowed", "denied" or "error: ..."

    @property
    def ok(self) -> bool:
        return self.outcome == self.expected


def _attempt(action: Callable[[], Any]) -> str:
    try:
        action()
        return "allowed"
    except Exception as exc:  # noqa: BLE001 - denials surface as driver or boto errors of many types
        text = f"{type(exc).__name__}: {exc}"
        denied = any(
            marker in text
            for marker in ("AuthorizationException", "not have privileges", "AccessDenied", "403")
        )
        return "denied" if denied else f"error: {text[:200]}"


def run(
    cursor_factory: Callable[[], Any], s3_client: Any | None, warehouse_table: str | None = None
) -> tuple[str, list[Check]]:
    """``warehouse_table`` (database.table) is a table the crawler must be able to
    read to build its dictionary; with none the check is reported as not run."""
    cursor = cursor_factory()
    cursor.execute("SELECT EFFECTIVE_USER()")
    user = str(cursor.fetchone()[0])

    checks: list[Check] = []
    sample: dict[str, Any] = {}

    def read_view() -> None:
        c = cursor_factory()
        c.execute(f"SELECT dataset_id, source_locator FROM {CRAWLABLE} LIMIT 1")
        row = c.fetchone()
        if row:
            sample["dataset_id"] = row[0]
            sample["locator"] = json.loads(row[1]) if isinstance(row[1], str) else row[1]

    checks.append(Check(f"SELECT from {CRAWLABLE}", "allowed", _attempt(read_view)))

    def read_warehouse() -> None:
        c = cursor_factory()
        c.execute(f"SELECT 1 FROM {warehouse_table} LIMIT 1")
        c.fetchall()

    if warehouse_table and all(_NAME.match(part) for part in warehouse_table.split(".")):
        checks.append(Check(f"SELECT from {warehouse_table}", "allowed", _attempt(read_warehouse)))
    else:
        checks.append(
            Check("SELECT from a warehouse table", "allowed", "not run: no mapping is active")
        )

    def write_index() -> None:
        # A permanent probe table: the crawler may create, insert and read in
        # helios_index, but is not granted DROP.
        c = cursor_factory()
        c.execute(
            "CREATE TABLE IF NOT EXISTS helios_index.access_probe "
            "(checked_by STRING, checked_at STRING) STORED AS ICEBERG"
        )
        c.execute(
            "INSERT INTO helios_index.access_probe VALUES (EFFECTIVE_USER(), CAST(NOW() AS STRING))"
        )
        c.execute("SELECT COUNT(*) FROM helios_index.access_probe")
        c.fetchall()

    checks.append(Check("create, write and read in helios_index", "allowed", _attempt(write_index)))
    for table in MUST_DENY_TABLES:

        def read_table(table: str = table) -> None:
            c = cursor_factory()
            c.execute(f"SELECT 1 FROM {table} LIMIT 1")
            c.fetchall()

        checks.append(Check(f"SELECT from {table}", "denied", _attempt(read_table)))

    locator = sample.get("locator")
    if s3_client is not None and locator and locator.get("connector_type") == "helios_ds_s3":
        bucket, key = locator["bucket"], locator["key"]
        prefix = key.split("datasets/", 1)[0]
        manifest = f"{prefix}_manifests/{sample['dataset_id']}/generation-manifest.json"
        checks.append(
            Check(
                "read an artifact object",
                "allowed",
                _attempt(lambda: s3_client.get_object(Bucket=bucket, Key=key)["Body"].read(1)),
            )
        )
        checks.append(
            Check(
                "read a generation manifest",
                "denied",
                _attempt(lambda: s3_client.get_object(Bucket=bucket, Key=manifest)["Body"].read(1)),
            )
        )
    else:
        checks.append(Check("S3 checks", "allowed", "error: no S3 client or no crawlable artifact"))
    return user, checks


def _mapped_table() -> str | None:
    """database.table of the first class in an active mapping, if there is one."""
    from helios_core.index import impala_index_store
    from helios_core.index import mappings as stored
    from helios_core.ontology.mapping import resolution_config

    try:
        index = impala_index_store()
        for record in stored.active(index).values() if index is not None else ():
            mapping = stored.mapping_of(record)
            if mapping.entities:
                return f"{resolution_config(mapping).database}.{mapping.entities[0].ossie_element}"
    except Exception as exc:  # noqa: BLE001 - the mapping tables are themselves under test here
        print(f"could not read the active mapping: {type(exc).__name__}: {exc}")
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--connection", default="S3 Object Store", help="Workbench data connection")
    parser.add_argument(
        "--warehouse-table",
        help="database.table the crawler must be able to read; default: the first table of the active mapping",
    )
    args = parser.parse_args(argv)

    from helios_core.config import impala_config
    from helios_core.engines.impala import ImpalaEngine

    config = impala_config()
    if config is None:
        print("Impala is not configured (IMPALA_HOST, WORKLOAD_USER, WORKLOAD_PASSWORD).")
        return 2
    connection = ImpalaEngine(config).connect()
    try:
        import cml.data_v1 as cmldata

        s3 = cmldata.get_connection(args.connection).get_base_connection()
    except Exception as exc:  # noqa: BLE001 - reported; the SQL checks still run
        print(f"S3 data connection {args.connection!r} unavailable: {type(exc).__name__}: {exc}")
        s3 = None

    try:
        user, checks = run(connection.cursor, s3, args.warehouse_table or _mapped_table())
    except Exception as exc:
        if "401" in str(exc) or "Unauthorized" in str(exc):
            print(
                f"Impala rejected the login for {config.user!r} (HTTP 401): authentication "
                "failed before any permission was checked. Check that the machine user has a "
                "workload password, an environment role (EnvironmentUser), that users were "
                "synchronised to the environment afterwards, and that WORKLOAD_USER is its "
                "exact workload username."
            )
            return 2
        raise
    print(f"Running as: {user}")
    for check in checks:
        mark = "PASS" if check.ok else "FAIL"
        print(f"  {mark}  {check.name}: expected {check.expected}, got {check.outcome}")
    failed = [c for c in checks if not c.ok]
    print("All access checks passed." if not failed else f"{len(failed)} check(s) failed.")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
