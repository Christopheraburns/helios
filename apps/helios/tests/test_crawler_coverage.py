"""Coverage signals (CG-10): what a crawl could not explain, without ground truth."""

import pytest
from apps.helios.crawler import coverage
from apps.helios.crawler.crawl import crawl
from crawler_samples import RETAIL_SETTINGS
from helios_core.crawler.settings import EMPTY_SETTINGS, CrawlerSettings
from helios_core.index.records import MentionRecord, SegmentRecord
from helios_core.index.store import duckdb_index_store
from test_crawler_llm import _connector
from test_crawler_resolution import CONFIG, _run, gazetteer, warehouse  # noqa: F401 - fixtures


def _crawl(tmp_path, warehouse, gazetteer, settings, config=CONFIG):  # noqa: F811
    index = duckdb_index_store()
    index.ensure_tables()
    run = crawl(
        index, _connector(tmp_path / str(id(settings))), "ds-1", actor="test",
        ontology_version="0.2.0", settings=None, settings_hash=settings.content_hash(),
        crawler_settings=settings, gazetteer=gazetteer, resolution=config,
        warehouse_cursor=warehouse.cursor,
    )
    rows = index.read(coverage.COVERAGE, {"crawl_run_id": run.crawl_run_id})
    return index, run, {(r.signal, r.value): r for r in rows}


def _without(**removed):
    document = RETAIL_SETTINGS.model_dump(mode="json", by_alias=True)
    for section, names in removed.items():
        key = "name" if section == "patterns" else "label"
        document[section] = [rule for rule in document[section] if rule[key] not in names]
    document["cases"]["identifiers"] = [
        n for n in document["cases"]["identifiers"] if n not in removed.get("patterns", ())
    ]
    return CrawlerSettings.model_validate(document)


def test_shapes_describe_identifiers_by_pattern():
    assert coverage.shape_of("RMA-3056773") == "AAA-9999999"
    assert coverage.shape_of("Wilma.Graham@t.edu") == "AAAAA.AAAAAA@A.AAA"
    found = [m.group() for m in coverage.IDENTIFIER_LIKE.finditer("Order ORD-4410, call 5551234567, a@b.io, the 3 items, $4.85")]
    assert found == ["ORD-4410", "5551234567", "a@b.io"]


def test_a_tuned_crawl_reports_little_and_records_its_totals(tmp_path, warehouse, gazetteer):  # noqa: F811
    _, run, rows = _crawl(tmp_path, warehouse, gazetteer, RETAIL_SETTINGS)
    for name in ("segments_without_mentions", "assets_without_links", "unmatched_identifiers", "unknown_labels", "cases_unresolved"):
        assert name in run.counts
    assert run.counts["assets_without_links"] == 0  # every document names something known
    assert run.counts["cases_unresolved"] == run.counts["cases"] - run.counts["cases_resolved"]
    assert ("unmatched_identifier", "AAA-9999999") not in rows  # the RMA pattern catches them
    assert run.counts["claims_dropped_weak_cue"] >= 0 and run.counts["claims_dropped_undefined"] == 0


def test_a_missing_pattern_or_label_is_obvious(tmp_path, warehouse, gazetteer):  # noqa: F811
    _, tuned, _ = _crawl(tmp_path, warehouse, gazetteer, RETAIL_SETTINGS)
    broken = _without(patterns={"return_authorization"}, pdf_labels={"Customer ID"})
    _, run, rows = _crawl(tmp_path, warehouse, gazetteer, broken)

    missed = rows["unmatched_identifier", "AAA-9999999"]
    assert missed.count >= 3 and missed.example.startswith("RMA-")
    assert run.counts["unmatched_identifiers"] > tuned.counts["unmatched_identifiers"]
    label = rows["unknown_label", "Customer ID"]
    assert label.count == 2 and len(label.example) == 16  # both reports; a value seen under it
    assert run.counts["unknown_labels"] > tuned.counts["unknown_labels"]
    assert run.counts["cases"] > tuned.counts["cases"]  # nothing links the documents any more


def test_with_no_rules_everything_is_unexplained(tmp_path, warehouse, gazetteer):  # noqa: F811
    chats_read = EMPTY_SETTINGS.model_copy(update={"analyzers": RETAIL_SETTINGS.analyzers})
    _, run, rows = _crawl(tmp_path, warehouse, None, chats_read, config=None)
    assert run.counts["mentions"] == 0
    assert run.counts["segments_without_mentions"] == run.counts["segments"] == 13
    assert run.counts["assets_without_links"] == 7
    assert {value for signal, value in rows if signal == "unmatched_identifier"} >= {"AAA-9999999"}
    assert any(signal == "unknown_label" for signal, _ in rows)


def test_dropped_claims_are_counted_by_reason(tmp_path, warehouse, gazetteer):  # noqa: F811
    document = RETAIL_SETTINGS.model_dump(mode="json", by_alias=True)
    del document["claims"]["predicates"]["REFUND_APPROVED"]  # its cue words stay
    undefined = CrawlerSettings.model_validate(document)
    _, run, _ = _crawl(tmp_path, warehouse, gazetteer, undefined)
    assert run.counts["claims_dropped_undefined"] > 0

    document = RETAIL_SETTINGS.model_dump(mode="json", by_alias=True)
    document["claims"]["predicates"]["REFUND_APPROVED"]["blocked_speakers"] = ["customer", "staff", "unknown"]
    _, run, _ = _crawl(tmp_path, warehouse, gazetteer, CrawlerSettings.model_validate(document))
    assert run.counts["claims_dropped_speaker"] > 0

    document = RETAIL_SETTINGS.model_dump(mode="json", by_alias=True)
    document["claims"]["case_classes"] = []
    for definition in document["claims"]["predicates"].values():
        for role in (definition["subject"], definition["object"]):
            if role["class"] == "Return":
                role["find"] = ["case"]
    _, run, _ = _crawl(tmp_path, warehouse, gazetteer, CrawlerSettings.model_validate(document))
    assert run.counts["claims_dropped_no_subject"] + run.counts["claims_dropped_no_object"] > 0
    assert run.counts["claims"] == 0


def test_only_the_commonest_examples_are_kept_and_a_mention_covers_its_text():
    run = _run()

    def segment(n, text):
        return SegmentRecord(crawl_run_id=run.crawl_run_id, segment_id=f"s{n}", asset_id=f"a{n}",
                             segment_type="email_body", ordinal=0, locator={}, text=text)

    many = [segment(i, " ".join(f"{'X' * (1 + k % 8)}{'-' * (k // 8 % 2)}{'1' * (3 + k // 16)}" for k in range(60))) for i in range(2)]
    counts, rows = coverage.measure(run, many, [], [], RETAIL_SETTINGS)
    assert len([r for r in rows if r.signal == "unmatched_identifier"]) == coverage.TOP
    assert counts["unmatched_identifiers"] == 120 and counts["segments_without_mentions"] == 2

    covered = segment(9, "Order ORD-4410 shipped.")
    mention = MentionRecord(crawl_run_id=run.crawl_run_id, mention_id="m", asset_id="a9", segment_id="s9",
                            surface_form="ORD-4410", locator={}, extractor="pattern", start_offset=6, end_offset=14)
    counts, rows = coverage.measure(run, [covered], [mention], [], RETAIL_SETTINGS)
    assert rows == [] and counts["unmatched_identifiers"] == 0 and counts["segments_without_mentions"] == 0


@pytest.fixture
def client(monkeypatch):
    from apps.helios.console import crawler, ontology
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    def make(index):
        monkeypatch.setitem(ontology._index, "store", index)
        app = FastAPI()
        app.include_router(crawler.crawler_router)
        return TestClient(app)

    return make


def test_the_run_detail_carries_the_examples(tmp_path, warehouse, gazetteer, client):  # noqa: F811
    index, run, _ = _crawl(tmp_path, warehouse, gazetteer, _without(patterns={"return_authorization"}))
    detail = client(index).get(f"/api/v1/crawler/runs/{run.crawl_run_id}").json()
    shapes = [c for c in detail["coverage"] if c["signal"] == "unmatched_identifier"]
    assert shapes[0]["count"] >= shapes[-1]["count"]  # commonest first
    assert any(c["value"] == "AAA-9999999" and c["example"].startswith("RMA-") for c in shapes)
    assert detail["counts"]["unmatched_identifiers"] == run.counts["unmatched_identifiers"]
    assert detail["mapping"] is None  # this test crawl was not given one to record
