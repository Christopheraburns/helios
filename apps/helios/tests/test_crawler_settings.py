"""CR-0e: crawler settings, validated, versioned in helios_index, edited by API."""

import itertools

import pytest
from helios_cortex._pending.crawler.settings import CrawlerSettings, ontology_problems
from helios_cortex._pending import crawler_settings as versions
from helios_cortex.index import runs
from helios_cortex.index.store import duckdb_index_store
from pydantic import ValidationError
from crawler_samples import RETAIL_SETTINGS



@pytest.fixture
def index():
    store = duckdb_index_store()
    store.ensure_tables()
    return store


def _doc(**changes):
    data = RETAIL_SETTINGS.model_dump(mode="json")
    data.update(changes)
    return data


def _clock():
    ticks = itertools.count()
    return lambda: f"2026-10-01T00:00:{next(ticks):02d}+00:00"


# --- the settings document ---------------------------------------------------------


def test_defaults_are_valid_and_cover_every_retail_predicate_and_text_type():
    assert (
        CrawlerSettings.model_validate(RETAIL_SETTINGS.model_dump(mode="json")) == RETAIL_SETTINGS
    )
    assert set(RETAIL_SETTINGS.claims.cues) == {
        "PACKAGING_DAMAGED",
        "RETURN_REASON",
        "REFUND_REQUESTED",
        "REFUND_APPROVED",
    }
    assert all(
        a.enabled
        for a in (
            RETAIL_SETTINGS.analyzers.pdf,
            RETAIL_SETTINGS.analyzers.email,
            RETAIL_SETTINGS.analyzers.chat,
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
    for p in RETAIL_SETTINGS.patterns:
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
        {"Item", "Return", "Customer", "Store", "Sale", "Brand", "Reason", "Contains", "HasReason", "PartyTo"},
        set(RETAIL_SETTINGS.claims.cues),
    )
    assert problems == [
        "unknown ontology class 'Spaceship'",
        "unknown claim predicate 'TELEPORTED'",
    ]


# --- versions in the lakehouse ------------------------------------------------------


def test_versions_are_numbered_immutable_and_deduplicated(index):
    tick = _clock()
    first, created = versions.save(index, RETAIL_SETTINGS, "alice", "defaults", tick)
    assert (first.version, created) == (1, True)
    same, created_again = versions.save(index, RETAIL_SETTINGS, "bob", "again", tick)
    assert (same.version, created_again, same.created_by) == (1, False, "alice")
    doc = _doc()
    doc["analyzers"]["pdf"]["max_pages"] = 5
    second, _ = versions.save(
        index, CrawlerSettings.model_validate(doc), "bob", "fewer pages", tick
    )
    assert second.version == 2
    assert versions.settings_of(versions.get(index, 1)) == RETAIL_SETTINGS


def test_active_is_empty_until_a_version_is_activated_then_follows_the_latest(index):
    tick = _clock()
    record, settings = versions.active(index)
    assert record is None and settings.is_empty()  # the engine has no rules of its own
    with pytest.raises(versions.UnknownSettingsVersion):
        versions.activate(index, 7, "alice", tick)
    versions.save(index, RETAIL_SETTINGS, "alice", "", tick)
    doc = _doc()
    doc["analyzers"]["chat"]["enabled"] = False
    versions.save(index, CrawlerSettings.model_validate(doc), "alice", "", tick)
    versions.activate(index, 2, "alice", tick)
    record, settings = versions.active(index)
    assert record.version == 2 and not settings.analyzers.chat.enabled
    versions.activate(index, 1, "alice", tick)
    assert versions.active(index)[0].version == 1


def test_crawl_runs_record_the_settings_version_and_hash(index):
    record, _ = versions.save(index, RETAIL_SETTINGS, "alice")
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
    assert (stored.settings_version, stored.settings_hash) == (1, RETAIL_SETTINGS.content_hash())
    assert stored.crawl_run_id == run.crawl_run_id


# --- the API --------------------------------------------------------------------------
