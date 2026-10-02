"""CR-2: list through the view, fetch by locator, verify, crawl incrementally."""

import hashlib
import json

import duckdb
import pytest
from apps.helios.crawler.connectors import HeliosDsConnector, object_reader
from apps.helios.crawler.crawl import crawl
from crawler_samples import chat_bytes, email_bytes, pdf_bytes
from helios_core.crawler.settings import DEFAULT_SETTINGS
from helios_core.index import runs
from helios_core.index.store import duckdb_index_store

DATASET = "ds-1"


@pytest.fixture
def corpus(tmp_path):
    """Three artifacts on disk, listed in a stand-in for helios_ds.crawlable_artifacts."""
    root = tmp_path / "objects"
    files = {
        "a-pdf": ("pdf", "application/pdf", pdf_bytes()),
        "b-email": ("email", "message/rfc822", email_bytes()),
        "c-chat": ("chat", "application/json", chat_bytes()),
    }
    con = duckdb.connect()
    con.execute("CREATE SCHEMA helios_ds")
    con.execute(
        "CREATE TABLE helios_ds.crawlable_artifacts (artifact_id VARCHAR, dataset_id VARCHAR, "
        "artifact_type VARCHAR, mime_type VARCHAR, source_locator VARCHAR, sha256 VARCHAR, "
        "size_bytes BIGINT, semantic_timestamp VARCHAR)"
    )
    for artifact_id, (kind, mime, data) in files.items():
        key = f"datasets/{DATASET}/artifacts/{artifact_id}"
        (root / key).parent.mkdir(parents=True, exist_ok=True)
        (root / key).write_bytes(data)
        locator = {"connector_type": "helios_ds_file", "root": str(root), "key": key}
        con.execute(
            "INSERT INTO helios_ds.crawlable_artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                artifact_id,
                DATASET,
                kind,
                mime,
                json.dumps(locator),
                hashlib.sha256(data).hexdigest(),
                len(data),
                "2001-06-14T10:00:00Z",
            ],
        )
    # Another dataset's artifact must not be listed.
    con.execute(
        "INSERT INTO helios_ds.crawlable_artifacts VALUES ('z', 'other', 'pdf', 'application/pdf', "
        "'{}', 'x', 1, NULL)"
    )
    return HeliosDsConnector(DATASET, {"dataset_id": DATASET}, con.cursor, object_reader()), root


@pytest.fixture
def index():
    store = duckdb_index_store()
    store.ensure_tables()
    return store


def _crawl(index, connector, analyze=None, full=False, settings=DEFAULT_SETTINGS):
    return crawl(
        index,
        connector,
        DATASET,
        actor="srv_helios_crawler",
        ontology_version="0.2.0",
        settings=None,
        settings_hash=settings.content_hash(),
        crawler_settings=settings,
        analyze=analyze,
        full=full,
    )


def _seen(target):
    return lambda run, fetched, segments, mentions: target.append(fetched.asset.asset_id)


def test_lists_only_the_dataset_and_maps_ontology_classes(corpus):
    connector, _ = corpus
    assets = connector.list_assets()
    assert [a.asset_id for a in assets] == ["a-pdf", "b-email", "c-chat"]
    assert [a.ontology_class for a in assets] == ["Document", "Message", "Message"]
    assert assets[0].asset_version_id == assets[0].sha256


def test_fetch_verifies_hash_size_and_presence(corpus):
    connector, root = corpus
    pdf, email, chat = connector.list_assets()
    assert connector.fetch(pdf).status == "fetched"
    path = root / email.locator["key"]
    path.write_bytes(path.read_bytes().swapcase())  # same size, new bytes
    assert connector.fetch(email).status == "integrity_failed"
    (root / chat.locator["key"]).unlink()
    assert connector.fetch(chat).status == "missing"


def test_second_crawl_carries_assets_and_segments_forward(corpus, index):
    connector, _root = corpus
    seen = []
    first = _crawl(index, connector, analyze=_seen(seen))
    assert first.status == "SUCCEEDED"
    assert first.counts["listed"] == 3 and first.counts["analyzed"] == 3
    assert first.settings_hash == DEFAULT_SETTINGS.content_hash()
    assert seen == ["a-pdf", "b-email", "c-chat"]

    seen.clear()
    second = _crawl(index, connector, analyze=_seen(seen))
    assert second.counts == {
        "listed": 3,
        "carried_forward": 3,
        "segments": first.counts["segments"],
        "mentions": 0,
    }
    assert seen == []  # nothing fetched or analyzed
    first_segments = index.read("helios_index.segments", {"crawl_run_id": first.crawl_run_id})
    second_segments = index.read("helios_index.segments", {"crawl_run_id": second.crawl_run_id})
    assert sorted(s.segment_id for s in first_segments) == sorted(
        s.segment_id for s in second_segments
    )

    full = _crawl(index, connector, full=True)
    assert full.counts["analyzed"] == 3

    rows = index.read("helios_index.assets", {"crawl_run_id": second.crawl_run_id})
    assert {r.status for r in rows} == {"carried_forward"}
    assert all(r.ontology_version == "0.2.0" for r in rows)


def test_changed_settings_reanalyze_unchanged_assets(corpus, index):
    from helios_core.crawler.settings import CrawlerSettings

    connector, _ = corpus
    _crawl(index, connector)
    doc = DEFAULT_SETTINGS.model_dump(mode="json")
    doc["analyzers"]["pdf"]["max_pages"] = 5
    changed = _crawl(index, connector, settings=CrawlerSettings.model_validate(doc))
    assert changed.counts["analyzed"] == 3 and "carried_forward" not in changed.counts


def test_an_integrity_failure_is_recorded_and_retried_next_time(corpus, index):
    connector, root = corpus
    _, email, _ = connector.list_assets()
    path = root / email.locator["key"]
    original = path.read_bytes()
    path.write_bytes(original.swapcase())
    first = _crawl(index, connector)
    assert first.counts["analyzed"] == 2 and first.counts["integrity_failed"] == 1
    path.write_bytes(original)
    second = _crawl(index, connector)
    # The damaged asset was not reusable, so it is fetched again; the others carry forward.
    assert second.counts["carried_forward"] == 2 and second.counts["analyzed"] == 1


def test_a_crashing_crawl_is_marked_failed_and_leaves_no_rows(corpus, index):
    connector, _ = corpus

    def explode(run, fetched, segments, mentions):
        raise RuntimeError("analyzer bug")

    with pytest.raises(RuntimeError):
        _crawl(index, connector, analyze=explode)
    [run] = runs.runs(index)
    assert run.status == "FAILED" and "analyzer bug" in run.error
    assert index.read("helios_index.assets") == []
    assert index.read("helios_index.segments") == []


def test_transient_read_errors_are_retried_then_recorded_as_fetch_failed(corpus):
    connector, _ = corpus
    pdf, _, _ = connector.list_assets()
    connector._sleep = lambda seconds: None
    real_read = connector._read_object
    failures = {"left": 2}

    def flaky(locator):
        if failures["left"]:
            failures["left"] -= 1
            raise ConnectionError("Failed to talk with all RAZ URLS")
        return real_read(locator)

    connector._read_object = flaky
    assert connector.fetch(pdf).status == "fetched"  # third attempt succeeds

    connector._read_object = lambda locator: (_ for _ in ()).throw(ConnectionError("down"))
    result = connector.fetch(pdf)
    assert result.status == "fetch_failed" and "after 3 attempts" in result.detail
