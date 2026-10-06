"""Searching crawled documents (CR-11): embeddings, claims lookup, the crawl hook."""

import pytest
from apps.helios.crawler.crawl import crawl
from helios_core.crawler.settings import DEFAULT_SETTINGS
from helios_core.index import evidence, runs
from helios_core.index.records import (
    ClaimEvidenceRecord,
    ClaimRecord,
    EntityRecord,
    RelationshipRecord,
    SegmentRecord,
)
from helios_core.index.store import duckdb_index_store
from test_crawler_connector import DATASET, corpus  # noqa: F401 - fixture

pytest.importorskip("lancedb")

WORDS = ["damaged", "refund", "store", "invoice"]


def fake_embedder(texts):
    """One dimension per known word, so similarity is predictable without a model."""
    return [[float(text.lower().count(word)) + 0.01 for word in WORDS] for text in texts]


def segment(run_id, segment_id, asset_id, text):
    return SegmentRecord(
        crawl_run_id=run_id,
        segment_id=segment_id,
        asset_id=asset_id,
        segment_type="email_body",
        ordinal=0,
        locator={"part": "body"},
        text=text,
    )


@pytest.fixture
def index():
    store = duckdb_index_store()
    store.ensure_tables()
    return store


def start(index, source="src-1"):
    return runs.start(
        index,
        connector="helios_ds",
        source=source,
        ontology_version="0.2.0",
        crawler_version="test",
        actor="tester",
    )


def test_search_returns_the_closest_passage_with_its_linked_entities(index, tmp_path):
    run = start(index)
    customer = EntityRecord(
        crawl_run_id=run.crawl_run_id,
        entity_id="ent-1",
        ontology_class="Customer",
        canonical_name="Angela Raymond",
        external_ids=["tpcds.customer:c_customer_sk=54201"],
    )
    about = RelationshipRecord(
        crawl_run_id=run.crawl_run_id,
        relationship_id="rel-1",
        relationship_type="About",
        source_kind="asset",
        source_id="email-1",
        target_kind="entity",
        target_id="ent-1",
        resolved_by="joint",
        confidence=1.0,
    )
    segments = [
        segment(run.crawl_run_id, "s1", "email-1", "The box arrived damaged."),
        segment(run.crawl_run_id, "s2", "email-2", "Please send the invoice."),
        segment(run.crawl_run_id, "s3", "email-3", "   "),
    ]
    path = str(tmp_path / "evidence")
    assert evidence.embed_run(run, segments, [customer], [about], embedder=fake_embedder, path=path) == 2

    found = evidence.search("damaged packaging", 2, embedder=fake_embedder, path=path)
    assert [f["segment_id"] for f in found] == ["s1", "s2"]
    assert found[0]["relevance"] > 0.9 > found[1]["relevance"]
    assert found[0]["locator"] == {"part": "body"}
    assert found[0]["entities"] == [
        {
            "entity_id": "ent-1",
            "class": "Customer",
            "name": "Angela Raymond",
            "keys": ["tpcds.customer:c_customer_sk=54201"],
        }
    ]
    assert found[1]["entities"] == []


def test_an_email_is_one_passage_and_a_chat_is_windows_of_messages():
    def part(asset, ordinal, kind, text, **structure):
        return SegmentRecord(
            crawl_run_id="r",
            segment_id=f"{asset}-{ordinal}",
            asset_id=asset,
            segment_type=kind,
            ordinal=ordinal,
            locator={"n": ordinal},
            text=text,
            structure=structure,
        )

    chat = [
        part("chat", i, "message", f"line {i}", sender_name="Rosa D.", role="agent")
        for i in range(8)
    ]
    found = evidence.passages(
        [
            part("mail", 2, "email_body", "The box arrived crushed."),
            part("mail", 0, "email_header", "Hope Buchanan <hope@example.com>"),
            part("mail", 1, "email_subject", "Damaged package"),
            part("subject-only", 0, "email_subject", "No body here"),
            *chat,
            part("pdf", 0, "page", "Return report"),
        ]
    )
    by_asset = {}
    for passage in found:
        by_asset.setdefault(passage["asset_id"], []).append(passage)

    [mail] = by_asset["mail"]
    assert mail["segment_id"] == "mail-2" and mail["segment_type"] == "email"
    assert mail["text"] == (
        "Subject: Damaged package\nFrom: Hope Buchanan <hope@example.com>\n\nThe box arrived crushed."
    )
    assert [p["text"] for p in by_asset["subject-only"]] == ["Subject: No body here"]
    first, second = by_asset["chat"]
    assert first["segment_type"] == "chat" and first["locator"] == {"n": 0, "messages": 6}
    assert first["text"].splitlines()[0] == "Rosa D. (agent): line 0"
    assert [len(p["text"].splitlines()) for p in (first, second)] == [6, 5]
    assert second["text"].splitlines()[-1].endswith("line 7")
    assert [(p["segment_type"], p["text"]) for p in by_asset["pdf"]] == [("page", "Return report")]


def test_search_returns_one_passage_per_document(index, tmp_path):
    run = start(index)
    path = str(tmp_path / "evidence")
    pages = [
        SegmentRecord(
            crawl_run_id=run.crawl_run_id,
            segment_id=f"{asset}-{page}",
            asset_id=asset,
            segment_type="page",
            ordinal=page,
            locator={"page": page + 1},
            text=text,
        )
        for asset, page, text in [
            ("pdf-1", 0, "damaged damaged"),
            ("pdf-1", 1, "damaged"),
            ("pdf-2", 0, "damaged refund"),
        ]
    ]
    evidence.embed_run(run, pages, embedder=fake_embedder, path=path)
    found = evidence.search("damaged", 5, embedder=fake_embedder, path=path)
    assert [f["asset_id"] for f in found] == ["pdf-1", "pdf-2"]


def test_a_new_crawl_replaces_only_its_own_sources_passages(index, tmp_path):
    path = str(tmp_path / "evidence")
    first = start(index, "src-1")
    other = start(index, "src-2")
    evidence.embed_run(first, [segment(first.crawl_run_id, "old", "a", "damaged")], embedder=fake_embedder, path=path)
    evidence.embed_run(other, [segment(other.crawl_run_id, "kept", "b", "damaged")], embedder=fake_embedder, path=path)
    second = start(index, "src-1")
    evidence.embed_run(second, [segment(second.crawl_run_id, "new", "a", "damaged")], embedder=fake_embedder, path=path)

    found = evidence.search("damaged", 10, embedder=fake_embedder, path=path)
    assert sorted(f["segment_id"] for f in found) == ["kept", "new"]
    only = evidence.search("damaged", 10, source="src-2", embedder=fake_embedder, path=path)
    assert [f["segment_id"] for f in only] == ["kept"]


def test_search_says_when_nothing_has_been_embedded(tmp_path):
    with pytest.raises(evidence.EvidenceUnavailable, match="no crawl has been embedded"):
        evidence.search("anything", embedder=fake_embedder, path=str(tmp_path / "empty"))


def test_entity_claims_come_from_the_latest_successful_crawl(index):
    def record(run, name):
        index.append(
            evidence.ENTITIES,
            [
                EntityRecord(
                    crawl_run_id=run.crawl_run_id,
                    entity_id="ent-1",
                    ontology_class="Customer",
                    canonical_name=name,
                    external_ids=["tpcds.customer:c_customer_sk=54201"],
                ),
                EntityRecord(
                    crawl_run_id=run.crawl_run_id,
                    entity_id="ent-2",
                    ontology_class="Return",
                    canonical_name="Return 166147",
                ),
            ],
        )
        index.append(
            evidence.CLAIMS,
            [
                ClaimRecord(
                    crawl_run_id=run.crawl_run_id,
                    claim_id="c1",
                    predicate="REFUND_REQUESTED",
                    subject_entity_id="ent-2",
                    object_entity_id="ent-1",
                    confidence=0.9,
                    extractor="cue",
                ),
                ClaimRecord(
                    crawl_run_id=run.crawl_run_id,
                    claim_id="c2",
                    predicate="PACKAGING_DAMAGED",
                    subject_entity_id="ent-2",
                    confidence=0.8,
                    extractor="cue",
                ),
            ],
        )
        index.append(
            evidence.CLAIM_EVIDENCE,
            [
                ClaimEvidenceRecord(
                    crawl_run_id=run.crawl_run_id,
                    claim_id="c1",
                    evidence_id="e1",
                    asset_id="email-1",
                    segment_id="s1",
                    locator={"part": "body"},
                    excerpt="When will the refund post?",
                )
            ],
        )
        return runs.finish(index, run, {"claims": 2})

    record(start(index), "Angela Raymond (old crawl)")
    record(start(index), "Angela Raymond")
    evidence._run_cache.clear()

    by_name = evidence.entity_claims(index, "angela")
    assert [e["name"] for e in by_name["entities"]] == ["Angela Raymond"]
    assert by_name["claims_total"] == 1
    claim = by_name["claims"][0]
    assert claim["predicate"] == "REFUND_REQUESTED"
    assert claim["subject"]["name"] == "Return 166147" and claim["object"]["name"] == "Angela Raymond"
    assert claim["evidence"] == [
        {"asset_id": "email-1", "locator": {"part": "body"}, "excerpt": "When will the refund post?"}
    ]

    by_key = evidence.entity_claims(index, "tpcds.customer:c_customer_sk=54201")
    assert by_key["claims_total"] == 1
    assert evidence.entity_claims(index, "Return 166147", "packaging_damaged")["claims_total"] == 1
    assert evidence.entity_claims(index, "nobody") == {
        "entity": "nobody",
        "entities": [],
        "claims_total": 0,
        "claims": [],
    }


def _crawl(index, connector, embed):
    return crawl(
        index,
        connector,
        DATASET,
        actor="srv_helios_crawler",
        ontology_version="0.2.0",
        settings=None,
        settings_hash=DEFAULT_SETTINGS.content_hash(),
        crawler_settings=DEFAULT_SETTINGS,
        embed=embed,
        request={"requested_by": "cloudera-workbench:alice", "note": "after the pattern fix"},
    )


def test_a_crawl_embeds_its_segments_and_keeps_its_note(corpus, index, tmp_path):  # noqa: F811
    connector, _ = corpus
    path = str(tmp_path / "evidence")
    run = _crawl(
        index,
        connector,
        lambda run, segments, entities, relationships: evidence.embed_run(
            run, segments, entities, relationships, embedder=fake_embedder, path=path
        ),
    )
    assert run.status == "SUCCEEDED"
    assert 0 < run.counts["embedded"] <= run.counts["segments"]
    assert len(evidence.search("store", 50, embedder=fake_embedder, path=path)) == run.counts["embedded"]
    assert runs.runs(index)[0].settings["request"]["note"] == "after the pattern fix"


def test_a_failed_embedding_is_counted_and_the_crawl_still_succeeds(corpus, index):  # noqa: F811
    connector, _ = corpus

    def broken(*_):
        raise RuntimeError("model unavailable")

    run = _crawl(index, connector, broken)
    assert run.status == "SUCCEEDED"
    assert run.counts["embedding_failed"] == 1 and "embedded" not in run.counts
    assert run.counts["analyzed"] == 3


def test_warm_up_loads_the_model_once_in_the_background(monkeypatch):
    import sys
    import types

    loads = []

    class FakeModel:
        def __init__(self, name):
            loads.append(name)

        def encode(self, texts, **_):
            import numpy

            return numpy.zeros((len(texts), 3))

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=FakeModel)
    )
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object())
    monkeypatch.setattr(evidence, "_models", {})
    thread = evidence.warm_up()
    thread.join(timeout=10)
    assert evidence.default_embedder(["again"]) == [[0.0, 0.0, 0.0]]
    assert loads == [evidence.MODEL_NAME]


def test_document_tools_need_an_authorized_caller():
    from apps.helios.mcp import server as mcp_server

    for result in (
        mcp_server.search_evidence("damage", model="sales"),
        mcp_server.entity_claims("angela", model="sales"),
    ):
        assert result["error"] == "authorization_denied"
