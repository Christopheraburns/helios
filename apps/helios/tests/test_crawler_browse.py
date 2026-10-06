"""Browsing from an ontology class to entities, documents and passages (CR-9)."""

import pytest
from helios_core.index import browse, evidence, runs
from helios_core.index.records import (
    AssetRecord,
    EntityLinkRecord,
    EntityRecord,
    MentionRecord,
    RelationshipRecord,
    SegmentRecord,
)
from helios_core.index.store import duckdb_index_store


@pytest.fixture
def index():
    store = duckdb_index_store()
    store.ensure_tables()
    browse._tables.clear()
    evidence._run_cache.clear()
    return store


def crawl(index, name="Angela Raymond", succeed=True):
    run = runs.start(
        index, connector="helios_ds", source="src-1", ontology_version="0.2.0",
        crawler_version="test", actor="tester",
    )
    rid = run.crawl_run_id

    def entity(entity_id, cls, entity_name, keys=()):
        return EntityRecord(crawl_run_id=rid, entity_id=entity_id, ontology_class=cls,
                            canonical_name=entity_name, external_ids=list(keys))

    def asset(asset_id, mime):
        return AssetRecord(
            crawl_run_id=rid, asset_id=asset_id, asset_version_id="v", connector="helios_ds",
            source="src-1", ontology_class="Message", mime_type=mime,
            source_locator={"key": f"path/to/{asset_id}.eml"}, size_bytes=1,
            semantic_timestamp="2001-06-17T09:57:32Z", status="analyzed",
        )

    def rel(rel_id, kind, source_kind, source_id, target_id):
        return RelationshipRecord(
            crawl_run_id=rid, relationship_id=rel_id, relationship_type=kind,
            source_kind=source_kind, source_id=source_id, target_kind="entity",
            target_id=target_id, resolved_by="joint", confidence=1.0,
        )

    index.append(evidence.ENTITIES, [
        entity("cust", "Customer", name, ["tpcds.customer:c_customer_sk=54201"]),
        entity("other", "Customer", "Zed Other"),
        entity("ret", "Return", "RMA-1"),
    ])
    index.append(browse.ASSETS, [asset("mail", "message/rfc822"), asset("case", "application/pdf"), asset("unrelated", "application/pdf")])
    index.append(browse.RELATIONSHIPS, [
        rel("r1", "Mentions", "asset", "mail", "cust"),
        rel("r2", "About", "asset", "case", "cust"),
        rel("r3", "Mentions", "asset", "unrelated", "other"),
        rel("r4", "PartyTo", "entity", "ret", "cust"),
    ])
    index.append(browse.SEGMENTS, [
        SegmentRecord(crawl_run_id=rid, segment_id="s1", asset_id="mail", segment_type="email_body",
                      ordinal=2, locator={"part": "body"}, text="Regards, Angela Raymond"),
        SegmentRecord(crawl_run_id=rid, segment_id="s2", asset_id="mail", segment_type="email_subject",
                      ordinal=1, locator={"part": "subject"}, text="No name here"),
    ])
    index.append(browse.MENTIONS, [
        MentionRecord(crawl_run_id=rid, mention_id="m1", asset_id="mail", segment_id="s1",
                      surface_form="Angela Raymond", locator={}, extractor="gazetteer",
                      start_offset=9, end_offset=23),
        MentionRecord(crawl_run_id=rid, mention_id="m2", asset_id="mail", segment_id="s2",
                      surface_form="No name", locator={}, extractor="pattern", start_offset=0, end_offset=7),
    ])
    index.append(browse.ENTITY_LINKS, [
        EntityLinkRecord(crawl_run_id=rid, link_id="l1", mention_id="m1", entity_id="cust",
                         link_type="SameAs", resolved_by="joint", score=1.0),
        EntityLinkRecord(crawl_run_id=rid, link_id="l2", mention_id="m2", entity_id="other",
                         link_type="SameAs", resolved_by="alias", score=0.9),
    ])
    return runs.finish(index, run, {"claims": 0}) if succeed else run


def test_class_entities_lists_the_latest_crawls_entities_by_document_count(index):
    crawl(index, name="Angela Raymond (old)")
    latest = crawl(index)
    crawl(index, name="Angela Raymond (unfinished)", succeed=False)

    found = browse.class_entities(index, ["customer"])
    assert found["total"] == 2
    assert [(e["name"], e["documents"], e["crawl_run_id"]) for e in found["entities"]] == [
        ("Angela Raymond", 2, latest.crawl_run_id),
        ("Zed Other", 1, latest.crawl_run_id),
    ]
    assert browse.class_entities(index, ["Customer"], "c_customer_sk=54201")["total"] == 1
    assert browse.class_entities(index, ["Customer", "Return"], limit=1)["total"] == 3
    assert browse.class_entities(index, ["Store"]) == {"total": 0, "entities": []}


def test_entity_documents_show_the_passages_that_mention_the_entity(index):
    run = crawl(index)
    found = browse.entity_documents(index, run.crawl_run_id, "cust")

    assert found["entity"]["keys"] == ["tpcds.customer:c_customer_sk=54201"]
    assert found["documents_total"] == 2
    about, mentioned = found["documents"]  # the document about the entity comes first
    assert (about["asset_id"], about["relationship"], about["passages"]) == ("case", "About", [])
    assert (mentioned["name"], mentioned["relationship"]) == ("mail.eml", "Mentions")
    [passage] = mentioned["passages"]  # the subject mentions someone else
    assert passage["text"][passage["mentions"][0]["start"] : passage["mentions"][0]["end"]] == "Angela Raymond"
    assert passage["mentions"][0]["resolved_by"] == "joint"
    assert found["related"] == [
        {"relationship": "PartyTo", "direction": "in", "entity_id": "ret", "class": "Return", "name": "RMA-1"}
    ]
    assert browse.entity_documents(index, run.crawl_run_id, "nobody") is None
    assert browse.entity_documents(index, "crawl_missing", "cust") is None
