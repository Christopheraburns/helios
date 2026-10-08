"""A domain the crawler was never written for (CG-9): an outpatient clinic,
described only by a mapping and crawler settings (``second_domain.py``), is
crawled and scored by the same code as the retail practice corpus.

This is an offline proof: a DuckDB warehouse and five documents. It shows the
code holds no retail assumption the clinic trips over; it does not measure
accuracy on a real second corpus.
"""

import hashlib
import json

import duckdb
import pytest
import second_domain as clinic
import shapes
from helios_core.crawler.settings import EMPTY_SETTINGS
from helios_core.index.store import duckdb_index_store

from apps.helios.crawler import evaluate as harness
from apps.helios.crawler.connectors import HeliosDsConnector, object_reader
from apps.helios.crawler.crawl import crawl
from apps.helios.crawler.gazetteer import Gazetteer

DATASET = "clinic-1"


def _connector(root):
    con = duckdb.connect()
    con.execute("CREATE SCHEMA helios_ds")
    con.execute(
        "CREATE TABLE helios_ds.crawlable_artifacts (artifact_id VARCHAR, dataset_id VARCHAR, "
        "artifact_type VARCHAR, mime_type VARCHAR, source_locator VARCHAR, sha256 VARCHAR, "
        "size_bytes BIGINT, semantic_timestamp VARCHAR)"
    )
    kinds = {"message/rfc822": "email", "application/json": "chat", "application/pdf": "pdf"}
    for name, (mime, data) in clinic.DOCUMENTS.items():
        key = f"documents/{name}"
        (root / key).parent.mkdir(parents=True, exist_ok=True)
        (root / key).write_bytes(data)
        locator = {"connector_type": "helios_ds_file", "root": str(root), "key": key}
        con.execute(
            "INSERT INTO helios_ds.crawlable_artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [name, DATASET, kinds[mime], mime, json.dumps(locator), hashlib.sha256(data).hexdigest(), len(data), None],
        )
    return HeliosDsConnector(DATASET, {"dataset_id": DATASET}, con.cursor, object_reader())


def _crawl(root, settings):
    warehouse = clinic.build_warehouse()
    index = duckdb_index_store()
    index.ensure_tables()
    run = crawl(
        index, _connector(root), DATASET, actor="test", ontology_version="1.0.0", settings=None,
        settings_hash=settings.content_hash(), crawler_settings=settings,
        gazetteer=Gazetteer.build(warehouse.cursor, clinic.CONFIG, clinic.CLASSES, settings.dictionary),
        resolution=clinic.CONFIG, warehouse_cursor=warehouse.cursor,
    )
    return run, harness.load_index(index, run.crawl_run_id)


@pytest.fixture(scope="module")
def crawled(tmp_path_factory):
    run, rows = _crawl(tmp_path_factory.mktemp("clinic"), clinic.SETTINGS)
    truth = harness.Truth(**clinic.answer_key(rows.segments))
    profile = harness.ScoringProfile.from_settings(clinic.DATABASE, clinic.SETTINGS)
    return run, rows, truth, harness.score(truth, rows, run, profile)


def test_the_clinic_crawls_with_no_code_of_its_own(crawled):
    run, rows, _truth, _metrics = crawled
    assert run.status == "SUCCEEDED"
    assert run.counts["analyzed"] == len(clinic.DOCUMENTS)
    assert run.counts["cases"] == 2 and run.counts["cases_resolved"] == 2
    # The chat export is in a layout the practice corpus does not use.
    assert sum(1 for s in rows.segments if s.segment_type == "message") == 3


def test_names_and_identifiers_are_found_and_resolved_to_clinic_records(crawled):
    _run, rows, truth, metrics = crawled
    found = harness.match_mentions(truth.entity_mentions, rows.mentions).found
    assert [m["surface_form"] for m in truth.entity_mentions if m["mention_id"] not in found] == []
    assert metrics["resolution"]["accuracy"] == 1.0
    assert metrics["resolution"]["sameas_wrong"] == 0
    # "Dana Kim" must not land on Dana Kimura, the other patient with that first name.
    patients = {i for e in rows.entities if e.ontology_class == "Patient" for i in e.external_ids}
    assert {i for i in patients if ":patient_id=" in i} == {"clinic.patients:patient_id=10", "clinic.patients:patient_id=11"}


def test_the_visit_anchor_ties_each_case_to_its_patient_and_clinician(crawled):
    _run, _rows, _truth, metrics = crawled
    assert metrics["cases"]["precision"] == 1.0 and metrics["cases"]["recall"] == 1.0
    by_type = metrics["relationships"]["by_type"]
    for edge in ("AttendedBy", "SeenBy"):
        assert by_type[edge]["recall"] == 1.0 and by_type[edge]["precision"] == 1.0
    assert metrics["relationships"]["unmapped_truth_predicates"] == {}


def test_clinic_claims_come_from_configured_cues_speakers_and_roles(crawled):
    _run, rows, _truth, metrics = crawled
    kind = {e.entity_id: e.ontology_class for e in rows.entities}
    claims = sorted((c.predicate, kind[c.subject_entity_id], kind[c.object_entity_id]) for c in rows.claims)
    # The patient's "I was not prescribed anything else" and the summary's
    # "No prescription issued." are not claims.
    assert claims == [
        ("FOLLOW_UP_REQUESTED", "Patient", "Visit"),
        ("FOLLOW_UP_REQUESTED", "Patient", "Visit"),
        ("PRESCRIPTION_ISSUED", "Visit", "Patient"),
    ]
    summary = harness.summarize(metrics)
    assert summary["claim_precision"] == 1.0 and summary["claim_recall"] == 1.0
    assert summary["evidence_agreement"] == 1.0


def test_without_the_clinic_settings_the_same_code_finds_no_identifiers_or_claims(tmp_path):
    """What the crawl finds comes from the configuration, not from the code. The
    mapping alone still gives it the names in the warehouse."""
    run, rows = _crawl(tmp_path, EMPTY_SETTINGS)
    assert run.status == "SUCCEEDED"
    # The chat export cannot even be read: its layout is configuration too.
    assert run.counts["type_mismatch"] == 1
    assert rows.claims == []
    assert not any(m.extractor == "pattern" for m in rows.mentions)


def test_no_retail_shape_is_left_in_crawler_code():
    assert shapes.scan() == {}
