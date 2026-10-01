"""CR-2: list through the view, fetch by locator, verify, crawl incrementally."""

import hashlib
import json

import duckdb
import pytest
from apps.helios.crawler.connector import HeliosDsConnector, object_reader
from apps.helios.crawler.crawl import crawl
from helios_core.crawler.settings import DEFAULT_SETTINGS
from helios_core.index import runs
from helios_core.index.store import duckdb_index_store

DATASET = "ds-1"


@pytest.fixture
def corpus(tmp_path):
    """Three artifacts on disk, listed in a stand-in for helios_ds.crawlable_artifacts."""
    root = tmp_path / "objects"
    files = {
        "a-pdf": ("pdf", "application/pdf", b"%PDF-1.4 report"),
        "b-email": ("email", "message/rfc822", b"From: x@y.z\n\nhello"),
        "c-chat": ("chat", "application/json", b'{"schema": "helios-ds/chat-thread/1.0"}'),
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
    return HeliosDsConnector(con.cursor, object_reader()), root


@pytest.fixture
def index():
    store = duckdb_index_store()
    store.ensure_tables()
    return store


def _crawl(index, connector, analyze=None, full=False):
    return crawl(
        index,
        connector,
        DATASET,
        actor="srv_helios_crawler",
        ontology_version="0.2.0",
        settings=None,
        settings_hash=DEFAULT_SETTINGS.content_hash(),
        analyze=analyze,
        full=full,
    )


def test_lists_only_the_dataset_and_maps_ontology_classes(corpus):
    connector, _ = corpus
    assets = connector.list_assets(DATASET)
    assert [a.asset_id for a in assets] == ["a-pdf", "b-email", "c-chat"]
    assert [a.ontology_class for a in assets] == ["Document", "Message", "Message"]
    assert assets[0].asset_version_id == assets[0].sha256


def test_fetch_verifies_hash_size_and_presence(corpus):
    connector, root = corpus
    pdf, email, chat = connector.list_assets(DATASET)
    assert connector.fetch(pdf).status == "fetched"
    (root / email.locator["key"]).write_bytes(b"From: x@y.z\n\nHELLO")  # same size, new bytes
    assert connector.fetch(email).status == "integrity_failed"
    (root / chat.locator["key"]).unlink()
    assert connector.fetch(chat).status == "missing"


def test_second_crawl_fetches_nothing_and_changed_assets_are_refetched(corpus, index):
    connector, _root = corpus
    seen = []
    first = _crawl(index, connector, analyze=lambda run, f: seen.append(f.asset.asset_id))
    assert first.status == "SUCCEEDED"
    assert first.counts == {"listed": 3, "fetched": 3}
    assert first.settings_hash == DEFAULT_SETTINGS.content_hash()
    assert seen == ["a-pdf", "b-email", "c-chat"]

    seen.clear()
    second = _crawl(index, connector, analyze=lambda run, f: seen.append(f.asset.asset_id))
    assert second.counts == {"listed": 3, "carried_forward": 3}
    assert seen == []  # nothing fetched or analyzed

    full = _crawl(index, connector, full=True)
    assert full.counts == {"listed": 3, "fetched": 3}

    rows = index.read("helios_index.assets", {"crawl_run_id": second.crawl_run_id})
    assert {r.status for r in rows} == {"carried_forward"}
    assert all(r.ontology_version == "0.2.0" for r in rows)


def test_an_integrity_failure_is_recorded_and_retried_next_time(corpus, index):
    connector, root = corpus
    _, email, _ = connector.list_assets(DATASET)
    original = (root / email.locator["key"]).read_bytes()
    (root / email.locator["key"]).write_bytes(original.upper())
    first = _crawl(index, connector)
    assert first.counts == {"listed": 3, "fetched": 2, "integrity_failed": 1}
    (root / email.locator["key"]).write_bytes(original)
    second = _crawl(index, connector)
    # The damaged asset was not reusable, so it is fetched again; the others carry forward.
    assert second.counts == {"listed": 3, "carried_forward": 2, "fetched": 1}


def test_a_crashing_crawl_is_marked_failed_and_leaves_no_rows(corpus, index):
    connector, _ = corpus

    def explode(run, fetched):
        raise RuntimeError("analyzer bug")

    with pytest.raises(RuntimeError):
        _crawl(index, connector, analyze=explode)
    [run] = runs.runs(index)
    assert run.status == "FAILED" and "analyzer bug" in run.error
    assert index.read("helios_index.assets") == []


def test_transient_read_errors_are_retried_then_recorded_as_fetch_failed(corpus):
    connector, _ = corpus
    pdf, _, _ = connector.list_assets(DATASET)
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
