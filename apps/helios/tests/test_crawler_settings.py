"""CR-0e: crawler settings, validated, versioned in helios_index, edited by API."""

import itertools
from pathlib import Path

import pytest
import yaml
from helios_core.crawler.settings import DEFAULT_SETTINGS, CrawlerSettings, ontology_problems
from helios_core.index import crawler_settings as versions
from helios_core.index import ontology_versions, runs
from helios_core.index.store import duckdb_index_store
from helios_core.ontology.mapping import load_mappings
from helios_core.ontology.parser import parse
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[3]
EXTENSION = REPO_ROOT / "ontology/customers/example-tenant/extension.yaml"


@pytest.fixture
def index():
    store = duckdb_index_store()
    store.ensure_tables()
    return store


def _doc(**changes):
    data = DEFAULT_SETTINGS.model_dump(mode="json")
    data.update(changes)
    return data


def _clock():
    ticks = itertools.count()
    return lambda: f"2026-10-01T00:00:{next(ticks):02d}+00:00"


# --- the settings document ---------------------------------------------------------


def test_defaults_are_valid_and_cover_every_retail_predicate_and_text_type():
    assert (
        CrawlerSettings.model_validate(DEFAULT_SETTINGS.model_dump(mode="json")) == DEFAULT_SETTINGS
    )
    assert set(DEFAULT_SETTINGS.claims.cues) == {
        "PACKAGING_DAMAGED",
        "RETURN_REASON",
        "REFUND_REQUESTED",
        "REFUND_APPROVED",
    }
    assert all(
        a.enabled
        for a in (
            DEFAULT_SETTINGS.analyzers.pdf,
            DEFAULT_SETTINGS.analyzers.email,
            DEFAULT_SETTINGS.analyzers.chat,
        )
    )


def test_default_patterns_find_what_the_corpus_contains():
    import re

    text = (
        "Re: blanched fragrances item (AAAAAAAAGLMDAAAA), receipt ending in 5079. "
        "Return # RMA-3056773, case CS-954939, ticket 205079, Wilma.Graham@t.edu, "
        "$310.40 on June 14, 2001."
    )
    found = {}
    for p in DEFAULT_SETTINGS.patterns:
        m = re.search(p.regex, text, re.IGNORECASE if p.ignore_case else 0)
        if m:
            found[p.name] = m.group(p.group)
    assert found == {
        "tpcds_business_id": "AAAAAAAAGLMDAAAA",
        "email_address": "Wilma.Graham@t.edu",
        "ticket_number": "205079",
        "receipt_tail": "5079",
        "return_authorization": "RMA-3056773",
        "support_case": "CS-954939",
        "money": "$310.40",
        "date": "June 14, 2001",
    }


@pytest.mark.parametrize(
    "change, message",
    [
        ({"patterns": [{"name": "bad", "regex": "(", "kind": "value"}]}, "regular expression"),
        ({"patterns": [{"name": "k", "regex": "x", "kind": "key"}]}, "columns"),
        ({"patterns": [{"name": "g", "regex": "x", "group": 2, "kind": "value"}]}, "group"),
        ({"unknown_section": {}}, "Extra inputs"),
    ],
)
def test_invalid_documents_are_rejected(change, message):
    with pytest.raises(ValidationError, match=message):
        CrawlerSettings.model_validate(_doc(**change))


def test_case_linking_must_name_existing_patterns():
    doc = _doc()
    doc["cases"]["identifiers"] = ["no_such_pattern"]
    with pytest.raises(ValidationError, match="unknown patterns"):
        CrawlerSettings.model_validate(doc)


def test_ontology_problems_name_unknown_classes_and_predicates():
    doc = _doc()
    doc["contextual"]["Spaceship"] = ["the ship"]
    doc["claims"]["cues"]["TELEPORTED"] = ["beamed"]
    settings = CrawlerSettings.model_validate(doc)
    problems = ontology_problems(
        settings,
        {"Item", "Return", "Customer", "Store", "Sale", "Brand"},
        set(DEFAULT_SETTINGS.claims.cues),
    )
    assert problems == [
        "unknown ontology class 'Spaceship'",
        "unknown claim predicate 'TELEPORTED'",
    ]


# --- versions in the lakehouse ------------------------------------------------------


def test_versions_are_numbered_immutable_and_deduplicated(index):
    tick = _clock()
    first, created = versions.save(index, DEFAULT_SETTINGS, "alice", "defaults", tick)
    assert (first.version, created) == (1, True)
    same, created_again = versions.save(index, DEFAULT_SETTINGS, "bob", "again", tick)
    assert (same.version, created_again, same.created_by) == (1, False, "alice")
    doc = _doc()
    doc["analyzers"]["pdf"]["max_pages"] = 5
    second, _ = versions.save(
        index, CrawlerSettings.model_validate(doc), "bob", "fewer pages", tick
    )
    assert second.version == 2
    assert versions.settings_of(versions.get(index, 1)) == DEFAULT_SETTINGS


def test_active_falls_back_to_defaults_and_follows_the_latest_activation(index):
    tick = _clock()
    assert versions.active(index) == (None, DEFAULT_SETTINGS)
    with pytest.raises(versions.UnknownSettingsVersion):
        versions.activate(index, 7, "alice", tick)
    versions.save(index, DEFAULT_SETTINGS, "alice", "", tick)
    doc = _doc()
    doc["analyzers"]["chat"]["enabled"] = False
    versions.save(index, CrawlerSettings.model_validate(doc), "alice", "", tick)
    versions.activate(index, 2, "alice", tick)
    record, settings = versions.active(index)
    assert record.version == 2 and not settings.analyzers.chat.enabled
    versions.activate(index, 1, "alice", tick)
    assert versions.active(index)[0].version == 1


def test_crawl_runs_record_the_settings_version_and_hash(index):
    record, _ = versions.save(index, DEFAULT_SETTINGS, "alice")
    run = runs.start(
        index,
        connector="helios_ds_s3",
        source="ds1",
        ontology_version="0.2.0",
        crawler_version="0.1.0",
        actor="srv",
        settings_version=record.version,
        settings_hash=record.content_hash,
    )
    [stored] = runs.runs(index)
    assert (stored.settings_version, stored.settings_hash) == (1, DEFAULT_SETTINGS.content_hash())
    assert stored.crawl_run_id == run.crawl_run_id


# --- the API --------------------------------------------------------------------------


@pytest.fixture
def client(index, monkeypatch):
    from apps.helios.console import crawler, ontology
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setitem(ontology._index, "store", index)
    monkeypatch.setattr(ontology, "_actor", lambda request: "cloudera-workbench:alice")
    app = FastAPI()
    app.include_router(crawler.crawler_router)
    return TestClient(app)


def _activate_ontology(index):
    graph = parse(
        str(EXTENSION),
        version="0.2.0",
        mappings=load_mappings(REPO_ROOT / "ontology/mappings/ossie"),
        ossie_model=yaml.safe_load((REPO_ROOT / "models/published/tpcds.ossie.yaml").read_text()),
    ).graph
    ontology_versions.publish(index, graph, "x.yaml", "alice")
    ontology_versions.activate(index, "0.2.0", "alice")


def test_api_shows_defaults_until_a_version_is_activated(client):
    body = client.get("/api/v1/crawler/settings").json()
    assert body["using_defaults"] and body["active_version"] is None and body["versions"] == []
    assert body["settings"] == client.get("/api/v1/crawler/settings/defaults").json()["settings"]


def test_api_saves_validates_and_activates(client, index):
    _activate_ontology(index)
    doc = _doc()
    doc["analyzers"]["pdf"]["max_pages"] = 10
    saved = client.post("/api/v1/crawler/settings", json={"settings": doc, "note": "fewer pages"})
    assert saved.status_code == 200, saved.text
    assert (
        saved.json()["version"] == 1 and saved.json()["created"] and saved.json()["warnings"] == []
    )

    bad = _doc(patterns=[{"name": "x", "regex": "(", "kind": "value"}])
    rejected = client.post("/api/v1/crawler/settings", json={"settings": bad})
    assert rejected.status_code == 422
    assert any("regular expression" in p for p in rejected.json()["detail"]["problems"])

    unknown = _doc()
    unknown["claims"]["cues"]["TELEPORTED"] = ["beamed"]
    rejected = client.post("/api/v1/crawler/settings", json={"settings": unknown})
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["problems"] == ["unknown claim predicate 'TELEPORTED'"]

    assert client.post("/api/v1/crawler/settings/9:activate").status_code == 404
    assert client.post("/api/v1/crawler/settings/1:activate").status_code == 200
    body = client.get("/api/v1/crawler/settings").json()
    assert body["active_version"] == 1 and body["settings"]["analyzers"]["pdf"]["max_pages"] == 10
    assert body["versions"][0]["active"] is True
    assert client.get("/api/v1/crawler/settings/versions/1").json()["note"] == "fewer pages"


def test_api_warns_when_no_ontology_is_active(client):
    saved = client.post("/api/v1/crawler/settings", json={"settings": _doc()})
    assert saved.status_code == 200
    assert "not checked" in saved.json()["warnings"][0]


def test_api_lists_runs_and_shows_one_with_its_assets(client, index):
    from helios_core.index.records import AssetRecord

    run = runs.start(
        index,
        connector="helios_ds_s3",
        source="ds1",
        ontology_version="0.2.0",
        crawler_version="0.1.0",
        actor="srv",
        settings_version=None,
        settings_hash="h",
    )
    index.append(
        "helios_index.assets",
        [
            AssetRecord(
                crawl_run_id=run.crawl_run_id,
                asset_id=a,
                asset_version_id="v",
                connector="helios_ds_s3",
                source="ds1",
                ontology_class=c,
                mime_type="application/pdf",
                source_locator={"key": f"k/{a}"},
                size_bytes=1,
                ontology_version="0.2.0",
                status=s,
            )
            for a, c, s in [("a1", "Document", "fetched"), ("a2", "Message", "fetch_failed")]
        ],
    )
    runs.finish(index, run, {"listed": 2, "fetched": 1, "fetch_failed": 1})
    [listed] = client.get("/api/v1/crawler/runs").json()
    assert listed["status"] == "SUCCEEDED" and listed["counts"]["listed"] == 2
    assert client.get("/api/v1/crawler/runs", params={"source": "other"}).json() == []
    detail = client.get(f"/api/v1/crawler/runs/{run.crawl_run_id}").json()
    assert detail["asset_counts"] == {
        "by_status": {"fetched": 1, "fetch_failed": 1},
        "by_class": {"Document": 1, "Message": 1},
    }
    assert [a["asset_id"] for a in detail["assets"]] == ["a1", "a2"]
    assert detail["assets"][1]["object_key"] == "k/a2"
    assert client.get("/api/v1/crawler/runs/nope").status_code == 404
