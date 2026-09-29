import datetime as dt
import decimal

import duckdb
import pytest

from helios_ds.scenarios import SCENARIOS
from helios_ds.tpcds import ImpalaTpcds, canonical_value, fingerprint_hash

TABLES = ("store_returns", "reason", "item")


@pytest.mark.parametrize(
    "raw, canonical",
    [
        ("Summit jacket   ", "Summit jacket"),
        (decimal.Decimal("12.30"), "12.30"),
        (dt.date(2001, 8, 14), "2001-08-14"),
        (7, 7),
        (None, None),
        (1.5, "1.5"),
    ],
)
def test_canonical_values(raw, canonical):
    assert canonical_value(raw) == canonical


def test_canonical_records_ignore_row_order(small_repo, make_shuffled_repo):
    definition = SCENARIOS["product_return_damage"]
    shuffled = make_shuffled_repo(definition.tables)

    def records(repo):
        return sorted(repo.query(definition.eligibility_sql), key=definition.business_key)

    assert records(shuffled) == records(small_repo)


def test_fingerprint_is_stable_and_detects_changes(small_repo, make_shuffled_repo):
    first = small_repo.fingerprint(TABLES)
    assert fingerprint_hash(small_repo.fingerprint(TABLES)) == fingerprint_hash(first)
    # Row order does not matter; content does.
    shuffled = make_shuffled_repo(TABLES)
    assert shuffled.fingerprint(TABLES) == first
    shuffled.con.sql("UPDATE item SET i_product_name = 'changed' WHERE i_item_sk = 1")
    changed = shuffled.fingerprint(TABLES)
    assert changed["tables"]["item"]["content_sha256"] != first["tables"]["item"]["content_sha256"]
    assert changed["tables"]["reason"] == first["tables"]["reason"]


def test_impala_backend_runs_the_same_eligibility_sql(small_repo, small_tpcds_path):
    """ImpalaTpcds only needs a DB-API connection; DuckDB stands in for Impala here."""
    impala = ImpalaTpcds(lambda: duckdb.connect(small_tpcds_path, read_only=True))
    definition = SCENARIOS["product_return_damage"]

    def records(repo):
        return sorted(repo.query(definition.eligibility_sql), key=definition.business_key)

    assert records(impala) == records(small_repo)
    assert impala.columns(definition.eligibility_sql) == small_repo.columns(
        definition.eligibility_sql
    )


class _FakeImpala:
    """Answers the three statements ImpalaTpcds.table_identity issues."""

    RESULTS = {
        "DESCRIBE item": (
            ["name", "type", "comment"],
            [("i_item_sk", "BIGINT", ""), ("i_item_id", "STRING", "")],
        ),
        "SELECT count(*) FROM item": (["count(*)"], [(18000,)]),
        "DESCRIBE HISTORY item": (
            ["creation_time", "snapshot_id", "parent_id", "is_current_ancestor"],
            [
                ("2026-09-01 10:00:00", 111, None, "TRUE"),
                ("2026-09-02 10:00:00", 222, 111, "TRUE"),
                ("2026-09-03 10:00:00", 333, 111, "FALSE"),  # rolled-back branch
            ],
        ),
    }

    def cursor(self):
        return self

    def execute(self, sql):
        columns, self._rows = self.RESULTS[sql]
        self.description = [(c,) for c in columns]

    def fetchall(self):
        return self._rows


def test_impala_table_identity_uses_the_current_iceberg_snapshot():
    identity = ImpalaTpcds(_FakeImpala).table_identity("item")
    assert identity["rows"] == 18000
    assert identity["snapshot_id"] == 222
    assert fingerprint_hash({"t": identity}) == fingerprint_hash({"t": dict(identity)})
