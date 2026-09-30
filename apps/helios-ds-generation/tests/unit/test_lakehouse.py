from pathlib import Path

import pytest

from helios_ds.lakehouse import TABLES, impala_ddl
from helios_ds.lakehouse.tables import DUCKDB, IMPALA, ddl_statements
from helios_ds.schemas import ScenarioPlanRecord, TemplateVersionRecord

DDL_FILE = Path(__file__).resolve().parents[2] / "schemas" / "lakehouse_impala.sql"

TRICKY = 'it\'s a "quoted" back\\slash,\nnewline, tab\t and ünïcode ✓'


def _plan(dataset_id="d1", scenario_id="s1", text="ought"):
    return ScenarioPlanRecord(
        dataset_id=dataset_id,
        scenario_id=scenario_id,
        scenario_type="product_return_damage",
        business_key="store_returns:sr_ticket_number=1|sr_item_sk=2",
        rank_score="00ff",
        scenario_seed="abcd",
        source_refs=[{"table": "item", "key": {"i_item_sk": 2}}],
        facts={"i_product_name": text, "sr_return_amt": "12.30", "missing": None},
        artifact_plan=[{"artifact_type": "pdf", "ordinal": 0}],
    )


def test_ensure_tables_is_idempotent_and_creates_every_table(make_sink):
    sink = make_sink()
    sink.ensure_tables()
    sink.ensure_tables()
    for table in TABLES:
        assert sink.read(table) == []


def test_round_trip_preserves_json_and_awkward_text(make_sink):
    sink = make_sink()
    sink.ensure_tables()
    sink.append("helios_ds.scenario_plans", [_plan("d1", "s1", TRICKY), _plan("d2", "s2")])
    assert sink.read_dataset("helios_ds.scenario_plans", "d1") == [_plan("d1", "s1", TRICKY)]


def test_large_appends_are_batched(make_sink, monkeypatch):
    from helios_ds.lakehouse import SqlLakehouseSink

    monkeypatch.setattr(SqlLakehouseSink, "MAX_ROWS_PER_INSERT", 7)
    sink = make_sink()
    sink.ensure_tables()
    sink.append("helios_ds.scenario_plans", [_plan("d1", f"s{i}") for i in range(30)])
    assert len(sink.read_dataset("helios_ds.scenario_plans", "d1")) == 30


def test_delete_dataset_rows_only_touches_that_dataset(make_sink):
    sink = make_sink()
    sink.ensure_tables()
    sink.append("helios_ds.scenario_plans", [_plan("d1", "s1"), _plan("d2", "s2")])
    sink.delete_dataset_rows("helios_ds.scenario_plans", "d1")
    sink.delete_dataset_rows("helios_ds.scenario_plans", "never-written")
    assert [r.dataset_id for r in sink.read("helios_ds.scenario_plans")] == ["d2"]


def test_append_rejects_wrong_record_type(make_sink):
    sink = make_sink()
    sink.ensure_tables()
    wrong = TemplateVersionRecord(
        dataset_id="d",
        template_id="t",
        template_version="1",
        template_schema_version="1.0",
        artifact_type="pdf",
        content_hash="h",
    )
    with pytest.raises(TypeError, match="ScenarioPlanRecord"):
        sink.append("helios_ds.scenario_plans", [wrong])


def test_unknown_table_is_rejected(make_sink):
    with pytest.raises(KeyError):
        make_sink().append("helios_index.assets", [])


def test_generator_never_defines_helios_index_tables():
    assert not [name for name in TABLES if name.startswith("helios_index.")]


def test_impala_ddl_creates_iceberg_v2_tables_in_both_databases():
    statements = ddl_statements(IMPALA)
    assert statements[0] == "CREATE DATABASE IF NOT EXISTS helios_ds"
    assert "CREATE DATABASE IF NOT EXISTS helios_ground_truth" in statements
    tables = [s for s in statements if s.startswith("CREATE TABLE")]
    assert len(tables) == len(TABLES)
    assert all(s.endswith("STORED AS ICEBERG TBLPROPERTIES ('format-version'='2')") for s in tables)
    assert not any("ICEBERG" in s for s in ddl_statements(DUCKDB))


def test_impyla_binds_awkward_text_as_escaped_impala_literals():
    """The Impala sink relies on impyla's client-side binding for quoting."""
    from impala.interface import _bind_parameters

    sql = _bind_parameters("INSERT INTO t VALUES (?, ?, ?)", [TRICKY, None, 5])
    assert sql == (
        "INSERT INTO t VALUES ('it\\'s a \\\"quoted\\\" back\\\\slash,\\nnewline, "
        "tab\t and ünïcode ✓', NULL, 5)"
    )


def test_committed_impala_ddl_is_current():
    assert DDL_FILE.read_text() == impala_ddl(), (
        "schemas/lakehouse_impala.sql is stale; regenerate with "
        "`python -m helios_ds.lakehouse > schemas/lakehouse_impala.sql`"
    )


def test_sql_sink_gives_each_thread_its_own_connection(tmp_path):
    """DB-API connections (impyla) are not thread-safe; the API uses a thread pool.
    Regression: a shared connection hung/500'd the manifest viewer (2026-09-29)."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import duckdb

    from helios_ds.lakehouse import SqlLakehouseSink

    path = str(tmp_path / "lakehouse.duckdb")
    owners = []

    class OwnedConnection:
        def __init__(self):
            self.owner = threading.get_ident()
            self.inner = duckdb.connect(path)
            owners.append(self.owner)

        def cursor(self):
            assert threading.get_ident() == self.owner, "connection used from another thread"
            return self.inner.cursor()

    sink = SqlLakehouseSink(OwnedConnection, DUCKDB)
    sink.ensure_tables()
    sink.append("helios_ds.scenario_plans", [_plan("d1", f"s{i}") for i in range(5)])
    with ThreadPoolExecutor(4) as pool:
        counts = list(pool.map(lambda _: len(sink.read("helios_ds.scenario_plans")), range(12)))
    assert counts == [5] * 12
    assert len(set(owners)) == len(owners) > 1


# Impala reserved words that could plausibly be chosen as column names. Regression:
# a `comment` column broke CREATE TABLE in Impala (DuckDB accepts it).
IMPALA_RESERVED = {
    "comment",
    "location",
    "partition",
    "range",
    "sort",
    "role",
    "rows",
    "row",
    "table",
    "column",
    "columns",
    "data",
    "date",
    "default",
    "format",
    "function",
    "group",
    "key_",
    "limit",
    "order",
    "schema",
    "select",
    "set",
    "stats",
    "timestamp",
    "values",
    "view",
    "change",
    "cache",
    "class",
    "current",
    "delete",
    "desc",
    "describe",
    "file",
    "files",
    "first",
    "last",
    "left",
    "right",
    "offset",
    "over",
    "replace",
    "update",
    "user",
    "with",
}


def test_no_column_name_is_an_impala_reserved_word():
    from helios_ds.lakehouse.tables import column_names

    for spec in TABLES.values():
        clashes = set(column_names(spec)) & IMPALA_RESERVED
        assert not clashes, (spec.full_name, clashes)
