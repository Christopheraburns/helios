"""Trying a draft rule on sample text (the Settings editor's "try it" panel)."""

import pytest
from apps.helios.crawler import tryout
from crawler_samples import RETAIL_SETTINGS
from helios_core.index import browse, runs
from helios_core.index.records import SegmentRecord
from helios_core.index.store import duckdb_index_store

TEXT = (
    "Return # RMA-3056773 and RMA-3056773 again. The refund was not approved. "
    "Later the refund was approved. If the refund is approved, tell the customer about the item."
)
EMAIL = tryout.Passage("p1", "email_body", {"part": "body"}, TEXT)
PAGE = tryout.Passage("p2", "page", {"page": 1}, "Report\nCustomer ID\nAAAAAAAADPDEAAAA\nBrand\nscholarnameless #8\n")


def test_a_pattern_shows_every_match_and_the_commonest_values():
    found = tryout.try_rule(RETAIL_SETTINGS, {"type": "pattern", "name": "return_authorization"}, [EMAIL, PAGE])
    assert (found["scanned"], found["matched"], found["matches"]) == (2, 1, 2)
    assert found["values"] == [{"text": "RMA-3056773", "count": 2}] and found["distinct_values"] == 1
    [passage] = found["passages"]
    assert [TEXT[m["start"] : m["end"]] for m in passage["matches"]] == ["RMA-3056773", "RMA-3056773"]
    assert passage["segment_type"] == "email_body" and not passage["truncated"]


def test_a_claim_shows_which_cues_fire_and_which_are_cancelled():
    found = tryout.try_rule(RETAIL_SETTINGS, {"type": "claim", "predicate": "REFUND_APPROVED"}, [EMAIL])
    marks = [(m["text"], m["blocked"]) for m in found["passages"][0]["matches"]]
    assert ("refund was not approved", True) in marks  # a negation inside the cue
    assert ("refund was approved", False) in marks
    assert ("refund is approved", True) in marks  # "If ...": a hedge earlier in the clause
    assert found["blocked"] == sum(1 for _, stopped in marks if stopped) > 0
    assert all(v["text"] != "refund was not approved" for v in found["values"])  # only live matches are counted


def test_labels_phrases_and_class_cues():
    label = tryout.try_rule(RETAIL_SETTINGS, {"type": "label", "label": "Customer ID"}, [EMAIL, PAGE])
    assert label["scanned"] == 1  # labels are read on pages, not in an email body
    assert label["values"] == [{"text": "AAAAAAAADPDEAAAA", "count": 1}]
    assert tryout.try_rule(RETAIL_SETTINGS, {"type": "label"}, [PAGE])["matches"] == 2  # every label

    phrases = tryout.try_rule(RETAIL_SETTINGS, {"type": "contextual", "class": "Item"}, [EMAIL])
    assert [m["rule"] for m in phrases["passages"][0]["matches"]] == ["the item"]
    cues = tryout.try_rule(RETAIL_SETTINGS, {"type": "class_cue", "class": "Store"}, [PAGE])
    # The preset's cues for a store: a warehouse ID nearby, or a "#".
    assert [m["text"] for m in cues["passages"][0]["matches"]] == ["AAAAAAAADPDEAAAA", "#"]
    assert tryout.try_rule(RETAIL_SETTINGS, {"type": "class_cue", "class": "Reason"}, [PAGE])["matches"] == 0


def test_a_draft_change_is_what_gets_tried_and_bad_requests_are_named():
    document = RETAIL_SETTINGS.model_dump(mode="json")
    document["patterns"].append(
        {"name": "order_number", "regex": r"\bORD-\d{4}\b", "group": 0, "kind": "document_id", "proposed_class": "Sale"}
    )
    draft = type(RETAIL_SETTINGS).model_validate(document)
    found = tryout.try_rule(draft, {"type": "pattern", "name": "order_number"}, tryout.pasted("See ORD-0042."))
    assert found["values"] == [{"text": "ORD-0042", "count": 1}]
    assert tryout.pasted("   ") == []
    with pytest.raises(ValueError, match="no pattern named 'order_number'"):
        tryout.try_rule(RETAIL_SETTINGS, {"type": "pattern", "name": "order_number"}, [EMAIL])
    with pytest.raises(ValueError, match="cannot try a rule of type 'dictionary'"):
        tryout.try_rule(RETAIL_SETTINGS, {"type": "dictionary"}, [EMAIL])


def test_long_passages_are_cut_and_only_the_first_few_are_returned():
    long_text = "RMA-0000001 " + "x" * 3000 + " RMA-0000002"
    many = [tryout.Passage(f"p{i}", "text", {}, f"RMA-{i:07d}") for i in range(40)]
    found = tryout.try_rule(
        RETAIL_SETTINGS, {"type": "pattern", "name": "return_authorization"}, [tryout.Passage("long", "text", {}, long_text), *many]
    )
    assert (found["matched"], found["matches"], len(found["passages"])) == (41, 42, tryout.MAX_PASSAGES)
    first = found["passages"][0]
    assert first["truncated"] and len(first["text"]) == tryout.MAX_TEXT and len(first["matches"]) == 1
    assert found["distinct_values"] == 40 and len(found["values"]) == tryout.MAX_VALUES  # two repeat


@pytest.fixture
def client(monkeypatch):
    from apps.helios.console import crawler, ontology
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    index = duckdb_index_store()
    index.ensure_tables()
    browse._tables.clear()
    monkeypatch.setitem(ontology._index, "store", index)
    app = FastAPI()
    app.include_router(crawler.crawler_router)
    return TestClient(app), index


def test_the_api_tries_pasted_text_or_the_latest_crawl_and_says_when_there_is_none(client):
    api, index = client
    url = "/api/v1/crawler/settings:try"
    settings = RETAIL_SETTINGS.model_dump(mode="json")
    rule = {"type": "pattern", "name": "return_authorization"}

    nothing = api.post(url, json={"settings": settings, "rule": rule}).json()
    assert nothing["sample"]["kind"] == "none" and "even with no rules" in nothing["sample"]["reason"]
    assert nothing["scanned"] == 0

    pasted = api.post(url, json={"settings": settings, "rule": rule, "text": "RMA-1234567"}).json()
    assert pasted["sample"] == {"kind": "text"} and pasted["matches"] == 1

    # A crawl that ran with no rules at all still gives passages to try rules on.
    run = runs.start(index, connector="helios_ds", source="src-1", ontology_version="0.2.0", crawler_version="t", actor="t")
    index.append(
        browse.SEGMENTS,
        [SegmentRecord(crawl_run_id=run.crawl_run_id, segment_id="s1", asset_id="a", segment_type="email_body",
                       ordinal=0, locator={"part": "body"}, text="About RMA-7654321.")],
    )
    runs.finish(index, run, {"segments": 1, "mentions": 0})
    crawled = api.post(url, json={"settings": settings, "rule": rule}).json()
    assert crawled["sample"]["kind"] == "crawl" and crawled["sample"]["crawl_runs"][0]["source"] == "src-1"
    assert crawled["values"] == [{"text": "RMA-7654321", "count": 1}]
    assert api.post(url, json={"settings": settings, "rule": rule, "source": "other"}).json()["sample"]["kind"] == "none"

    broken = {**settings, "patterns": [{"name": "x", "regex": "(", "kind": "value"}]}
    refused = api.post(url, json={"settings": broken, "rule": rule})
    assert refused.status_code == 422 and "not a valid regular expression" in refused.json()["detail"]["problems"][0]
    assert api.post(url, json={"settings": settings, "rule": {"type": "nonsense"}}).status_code == 422

    listed = api.get("/api/v1/crawler/settings").json()
    assert listed["vocabulary"] is None  # no ontology version is active in this index
