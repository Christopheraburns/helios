"""CR-1: the helios_index schema, store and crawl-run bookkeeping."""

import itertools
from pathlib import Path

import pytest
from helios_core.index import RUN_TABLES, TABLES, ddl_statements, ids, records, runs
from helios_core.index.__main__ import impala_ddl
from helios_core.index.store import duckdb_index_store

DDL_FILE = Path(__file__).resolve().parents[3] / "shared/helios_core/index/schema_impala.sql"
RUN = "crawl_test"


@pytest.fixture
def index():
    store = duckdb_index_store()
    store.ensure_tables()
    return store


def test_ddl_file_is_current():
    assert DDL_FILE.read_text() == impala_ddl(), (
        "schema_impala.sql is stale; regenerate with "
        "`PYTHONPATH=shared python -m helios_core.index ddl > shared/helios_core/index/schema_impala.sql`"
    )


def test_every_index_table_is_iceberg_and_run_tables_carry_the_run_and_ontology():
    statements = ddl_statements()
    for table in TABLES:
        assert any(f"TABLE IF NOT EXISTS {table} " in s and "ICEBERG" in s for s in statements)
    assert set(RUN_TABLES) >= {
        "helios_index.assets",
        "helios_index.segments",
        "helios_index.mentions",
        "helios_index.entities",
        "helios_index.entity_links",
        "helios_index.relationships",
        "helios_index.claims",
        "helios_index.claim_evidence",
    }
    for table in RUN_TABLES:
        fields = TABLES[table].model_fields
        assert "ontology_version" in fields, table


def _samples():
    seg = ids.segment_id("sha-1", 0)
    men = ids.mention_id(seg, 10, 18, "Midway store", "gazetteer")
    ent = ids.entity_id("Store", "tpcds.store:s_store_sk=4")
    clm = ids.claim_id("PACKAGING_DAMAGED", ent, None)
    common = {"crawl_run_id": RUN, "ontology_version": "0.2.0"}
    return {
        "helios_index.assets": records.AssetRecord(
            **common,
            asset_id="a1",
            asset_version_id="sha-1",
            connector="helios_ds_s3",
            source="ds1",
            ontology_class="Message",
            mime_type="message/rfc822",
            source_locator={"bucket": "b", "key": "k"},
            size_bytes=10,
        ),
        "helios_index.segments": records.SegmentRecord(
            **common,
            segment_id=seg,
            asset_id="a1",
            segment_type="email_body",
            ordinal=0,
            locator={"part": "body"},
            text="I returned it to your Midway store.",
        ),
        "helios_index.mentions": records.MentionRecord(
            **common,
            mention_id=men,
            asset_id="a1",
            segment_id=seg,
            surface_form="Midway store",
            locator={"part": "body", "start": 10, "end": 18},
            proposed_class="Store",
            extractor="gazetteer",
            extractor_detail="s_city",
            start_offset=10,
            end_offset=18,
        ),
        "helios_index.entities": records.EntityRecord(
            **common,
            entity_id=ent,
            ontology_class="Store",
            canonical_name="ought (AAAA)",
            external_ids=["tpcds.store:s_store_sk=4"],
        ),
        "helios_index.entity_links": records.EntityLinkRecord(
            **common,
            link_id=ids.link_id(men, ent),
            mention_id=men,
            entity_id=ent,
            link_type="SameAs",
            resolved_by="joint",
            score=0.97,
            evidence_segment_ids=[seg],
        ),
        "helios_index.relationships": records.RelationshipRecord(
            **common,
            relationship_id=ids.relationship_id("Mentions", "a1", ent),
            relationship_type="Mentions",
            source_kind="asset",
            source_id="a1",
            target_kind="entity",
            target_id=ent,
            resolved_by="joint",
            confidence=0.97,
        ),
        "helios_index.claims": records.ClaimRecord(
            **common,
            claim_id=clm,
            predicate="PACKAGING_DAMAGED",
            subject_entity_id=ent,
            confidence=0.9,
            extractor="phrase_rules",
        ),
        "helios_index.claim_evidence": records.ClaimEvidenceRecord(
            **common,
            claim_id=clm,
            evidence_id=ids.evidence_id(clm, seg, 0, 35),
            asset_id="a1",
            segment_id=seg,
            locator={"part": "body", "start": 0, "end": 35},
            excerpt="I returned it to your Midway store.",
        ),
    }


def test_every_table_round_trips_including_json_columns(index):
    for table, record in _samples().items():
        index.append(table, [record])
        assert index.read(table, {"crawl_run_id": RUN}) == [record], table


def test_inserts_are_batched(index, monkeypatch):
    import helios_core.index.store as store_module

    statements = []
    original = index._execute

    def counting(sql, params=None):
        if sql.startswith("INSERT"):
            statements.append(sql)
        return original(sql, params)

    monkeypatch.setattr(index, "_execute", counting)
    monkeypatch.setattr(store_module, "MAX_BATCH_ROWS", 4)
    sample = _samples()["helios_index.entities"]
    rows = [sample.model_copy(update={"entity_id": f"e{i}"}) for i in range(10)]
    index.append("helios_index.entities", rows)
    assert len(statements) == 3  # 4 + 4 + 2
    assert len(index.read("helios_index.entities")) == 10


def test_run_lifecycle_and_failed_runs_leave_no_rows(index):
    ticks = itertools.count()
    clock = lambda: f"2026-10-01T00:00:{next(ticks):02d}+00:00"
    ok = runs.start(
        index,
        connector="helios_ds_s3",
        source="ds1",
        ontology_version="0.2.0",
        crawler_version="0.1.0",
        actor="srv",
        clock=clock,
    )
    runs.finish(index, ok, {"assets": 3}, clock)
    bad = runs.start(
        index,
        connector="helios_ds_s3",
        source="ds1",
        ontology_version="0.2.0",
        crawler_version="0.1.0",
        actor="srv",
        clock=clock,
    )
    asset = _samples()["helios_index.assets"].model_copy(update={"crawl_run_id": bad.crawl_run_id})
    index.append("helios_index.assets", [asset])
    runs.fail(index, bad, "boom", clock)
    states = {r.crawl_run_id: r.status for r in runs.runs(index)}
    assert states == {ok.crawl_run_id: "SUCCEEDED", bad.crawl_run_id: "FAILED"}
    assert index.read("helios_index.assets", {"crawl_run_id": bad.crawl_run_id}) == []
    assert runs.latest_succeeded(index, "ds1").counts == {"assets": 3}


def test_ids_are_stable_and_distinct():
    assert ids.entity_id("Store", "tpcds.store:s_store_sk=4") == ids.entity_id(
        "Store", "tpcds.store:s_store_sk=4"
    )
    assert ids.entity_id("Store", "tpcds.store:s_store_sk=4") != ids.entity_id(
        "Store", "tpcds.store:s_store_sk=5"
    )
    assert ids.external_id("tpcds", "store_returns", {"sr_ticket_number": 7, "sr_item_sk": 2}) == (
        "tpcds.store_returns:sr_item_sk=2,sr_ticket_number=7"
    )


def test_no_column_name_is_an_impala_reserved_word():
    from helios_core.index.tables import IMPALA_RESERVED, columns

    for table, model in TABLES.items():
        clashes = {name for name, _ in columns(model)} & IMPALA_RESERVED
        assert not clashes, (table, clashes)
