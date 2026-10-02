"""CR-8 / CR-E1: the evaluation harness, scored on a synthetic run and synthetic truth."""

from __future__ import annotations

import itertools
import json

import duckdb
import pytest
from apps.helios.crawler import evaluate as harness
from helios_core.index import ids, runs
from helios_core.index.records import (
    AssetRecord,
    ClaimEvidenceRecord,
    ClaimRecord,
    EntityLinkRecord,
    EntityRecord,
    MentionRecord,
    RelationshipRecord,
    SegmentRecord,
)
from helios_core.index.store import duckdb_index_store

DATASET = "ds-1"
ONT = "0.2.0"

# Ground-truth entities: (entity_id, type, source_key)
TRUTH_ENTITIES = {
    "E_cust": ("Customer", {"c_customer_sk": 96292, "table": "customer"}),
    "E_item": ("Item", {"i_item_sk": 2606, "table": "item"}),
    "E_ret": ("Return", {"sr_item_sk": 2606, "sr_ticket_number": 234309, "table": "store_returns"}),
    "E_sale": ("Sale", {"ss_item_sk": 2606, "ss_ticket_number": 234309, "table": "store_sales"}),
    "E_store": ("Store", {"s_store_sk": 4, "table": "store"}),
    "E_reason": ("Reason", {"r_reason_sk": 1, "table": "reason"}),
    "E_other": ("Item", {"i_item_sk": 1390, "table": "item"}),
    "E_art_email": ("Artifact", {"artifact_id": "a-email"}),
    "E_art_chat": ("Artifact", {"artifact_id": "b-chat"}),
    "E_art_pdf": ("Artifact", {"artifact_id": "c-pdf"}),
}
KEY = {name: harness.truth_key({"source_key": key}) for name, (_, key) in TRUTH_ENTITIES.items()}

HEADER = "Wilma Graham <Wilma.Graham@t.edu>"
SUBJECT = "Return RMA-4853189 damaged item"
BODY = (
    "I returned the blanched fragrances item (AAAAAAAAGLMDAAAA) to your Midway store.\n"
    "The box was completely crushed and the product is unusable."
)
MSG1 = "Hi, this is Dr. Carlos about receipt ending in 4309."
MSG2 = "It did. Packaging damage, looks like the item was dropped."
PAGE = (
    "Return Authorization Report\nCustomer ID\nAAAAAAAABIKFBAAA\n"
    "Recorded return reason: Package was damaged.\n"
)


def span(text: str, needle: str) -> tuple[int, int]:
    start = text.index(needle)
    return start, start + len(needle)


# --- the crawler's run -------------------------------------------------------------------

SEG = {
    "hdr": ("a-email", "email_header", {"header": "From"}, HEADER),
    "subj": ("a-email", "email_subject", {"part": "subject"}, SUBJECT),
    "body": ("a-email", "email_body", {"part": "body", "line_endings": "LF"}, BODY),
    "m1": ("b-chat", "message", {"message_id": "msg-1"}, MSG1),
    "m2": ("b-chat", "message", {"message_id": "msg-2"}, MSG2),
    "p1": ("c-pdf", "page", {"page": 1}, PAGE),
}
ENT = {
    "item": ("Item", KEY["E_item"]),
    "ret": ("Return", KEY["E_ret"]),
    "cust": ("Customer", KEY["E_cust"]),
    "cust_wrong": ("Customer", "tpcds.customer:c_customer_sk=999"),
    "sale": ("Sale", KEY["E_sale"]),
    "store": ("Store", KEY["E_store"]),
    "reason": ("Reason", KEY["E_reason"]),
}
# crawler mentions: name -> (segment, surface, class, extractor, link entity, link type, resolver)
CRAWLER_MENTIONS = {
    "cm1": ("body", "blanched fragrances", "Item", "gazetteer", "item", "SameAs", "exact_key"),
    "cm2": ("subj", "RMA-4853189", "Return", "pattern", "ret", "SameAs", "exact_key"),
    "cm3": ("hdr", HEADER, "Customer", "pattern", "cust_wrong", "SameAs", "fuzzy"),
    "cm4": ("m1", "Dr. Carlos", "Customer", "gazetteer", "cust", "SameAs", "alias"),
    "cm5": ("m1", "receipt ending in 4309", "Sale", "pattern", "sale", "PossiblySameAs", "joint"),
    "cm6": ("p1", "AAAAAAAABIKFBAAA", "Customer", "pattern", "cust", "SameAs", "exact_key"),
    "cm7": ("body", "unusable", None, "pattern", None, None, None),
}


def build_run(index, run_id: str | None = None) -> str:
    run = runs.start(
        index,
        connector="helios_ds",
        source=DATASET,
        ontology_version=ONT,
        crawler_version="0.3.0",
        actor="srv_helios_crawler",
        settings={"strategy": "deterministic"},
        settings_version=1,
    )
    rid = run.crawl_run_id
    common = {"crawl_run_id": rid, "ontology_version": ONT}
    index.append(
        "helios_index.assets",
        [
            AssetRecord(
                **common,
                asset_id=a,
                asset_version_id=f"v-{a}",
                connector="helios_ds",
                source=DATASET,
                ontology_class=cls,
                mime_type=mime,
                source_locator={"key": a},
                size_bytes=1,
                status="analyzed",
            )
            for a, cls, mime in (
                ("a-email", "Message", "message/rfc822"),
                ("b-chat", "Message", "application/json"),
                ("c-pdf", "Document", "application/pdf"),
            )
        ],
    )
    seg_ids = {
        name: ids.segment_id(f"v-{asset}", i) for i, (name, (asset, *_)) in enumerate(SEG.items())
    }
    index.append(
        "helios_index.segments",
        [
            SegmentRecord(
                **common,
                segment_id=seg_ids[name],
                asset_id=asset,
                segment_type=kind,
                ordinal=i,
                locator=locator,
                text=text,
            )
            for i, (name, (asset, kind, locator, text)) in enumerate(SEG.items())
        ],
    )
    entity_ids = {name: ids.entity_id(cls, key) for name, (cls, key) in ENT.items()}
    index.append(
        "helios_index.entities",
        [
            EntityRecord(
                **common, entity_id=entity_ids[n], ontology_class=cls, canonical_name=n, external_ids=[key]
            )
            for n, (cls, key) in ENT.items()
        ],
    )
    mentions, links = [], []
    for seg, surface, cls, extractor, entity, link_type, resolver in CRAWLER_MENTIONS.values():
        asset, _, locator, text = SEG[seg]
        start, end = span(text, surface)
        mention_locator = {**locator, "start": start, "end": end}
        if "page" in locator:
            mention_locator["text"] = surface
        mid = ids.mention_id(seg_ids[seg], start, end, surface, extractor)
        mentions.append(
            MentionRecord(
                **common,
                mention_id=mid,
                asset_id=asset,
                segment_id=seg_ids[seg],
                surface_form=surface,
                locator=mention_locator,
                proposed_class=cls,
                extractor=extractor,
                start_offset=start,
                end_offset=end,
            )
        )
        if entity:
            links.append(
                EntityLinkRecord(
                    **common,
                    link_id=ids.link_id(mid, entity_ids[entity]),
                    mention_id=mid,
                    entity_id=entity_ids[entity],
                    link_type=link_type,
                    resolved_by=resolver,
                    score=0.9,
                    evidence_segment_ids=[seg_ids[seg]],
                )
            )
    index.append("helios_index.mentions", mentions)
    index.append("helios_index.entity_links", links)

    def rel(rtype, skind, sid, tkind, tid):
        return RelationshipRecord(
            **common,
            relationship_id=ids.relationship_id(rtype, sid, tid),
            relationship_type=rtype,
            source_kind=skind,
            source_id=sid,
            target_kind=tkind,
            target_id=tid,
            resolved_by="joint",
            confidence=0.9,
        )

    e = entity_ids
    index.append(
        "helios_index.relationships",
        [
            rel("Mentions", "asset", "a-email", "entity", e["item"]),
            rel("About", "asset", "a-email", "entity", e["ret"]),
            rel("About", "asset", "b-chat", "entity", e["ret"]),
            rel("About", "asset", "c-pdf", "entity", e["ret"]),
            rel("PartyTo", "entity", e["sale"], "entity", e["cust"]),  # PURCHASED_IN, reversed
            rel("PartyTo", "entity", e["ret"], "entity", e["cust"]),  # RETURNED_BY
            rel("Contains", "entity", e["sale"], "entity", e["item"]),  # CONTAINS
            rel("LocatedAt", "entity", e["ret"], "entity", e["store"]),  # RETURNED_AT
            rel("HasReason", "entity", e["ret"], "entity", e["reason"]),  # HAS_REASON
        ],
    )
    c1 = ids.claim_id("PACKAGING_DAMAGED", e["item"], e["ret"])
    c2 = ids.claim_id("RETURN_REASON", e["ret"], e["reason"])
    c3 = ids.claim_id("REFUND_APPROVED", e["ret"], e["cust"])
    index.append(
        "helios_index.claims",
        [
            ClaimRecord(**common, claim_id=c1, predicate="PACKAGING_DAMAGED", subject_entity_id=e["item"], object_entity_id=e["ret"], confidence=0.9, extractor="cues"),
            ClaimRecord(**common, claim_id=c2, predicate="RETURN_REASON", subject_entity_id=e["ret"], object_entity_id=e["reason"], confidence=0.9, extractor="cues"),
            ClaimRecord(**common, claim_id=c3, predicate="REFUND_APPROVED", subject_entity_id=e["ret"], object_entity_id=e["cust"], confidence=0.9, extractor="cues"),
        ],
    )
    crushed = span(BODY, "The box was completely crushed")
    index.append(
        "helios_index.claim_evidence",
        [
            ClaimEvidenceRecord(
                **common,
                claim_id=c1,
                evidence_id=ids.evidence_id(c1, seg_ids["body"], *crushed),
                asset_id="a-email",
                segment_id=seg_ids["body"],
                locator={"part": "body", "start": crushed[0], "end": crushed[1]},
                excerpt="The box was completely crushed",
            ),
            ClaimEvidenceRecord(
                **common,
                claim_id=c2,
                evidence_id=ids.evidence_id(c2, seg_ids["p1"], None, None),
                asset_id="c-pdf",
                segment_id=seg_ids["p1"],
                locator={"page": 1, "text": "Package was damaged."},
                excerpt="Package was damaged.",
            ),
        ],
    )
    runs.finish(index, run, {"listed": 3, "analyzed": 3, "segments": 6, "mentions": 7})
    return rid


# --- the ground truth ----------------------------------------------------------------------

TRUTH_DDL = {
    "entities": "dataset_id VARCHAR, entity_id VARCHAR, entity_type VARCHAR, source_key VARCHAR, canonical_name VARCHAR",
    "entity_mentions": (
        "dataset_id VARCHAR, mention_id VARCHAR, entity_id VARCHAR, artifact_id VARCHAR, surface_form VARCHAR, "
        "modality VARCHAR, start_offset BIGINT, end_offset BIGINT, scenario_id VARCHAR, entity_type VARCHAR, "
        "locator VARCHAR, difficulty VARCHAR"
    ),
    "relationships": (
        "dataset_id VARCHAR, relationship_id VARCHAR, source_entity_id VARCHAR, predicate VARCHAR, "
        "target_entity_id VARCHAR, confidence DOUBLE, scenario_id VARCHAR, artifact_id VARCHAR"
    ),
    "claims": (
        "dataset_id VARCHAR, claim_id VARCHAR, scenario_id VARCHAR, claim_type VARCHAR, subject VARCHAR, "
        "obj VARCHAR, truth_status VARCHAR, statement VARCHAR"
    ),
    "evidence": (
        "dataset_id VARCHAR, evidence_id VARCHAR, claim_id VARCHAR, artifact_id VARCHAR, segment_id VARCHAR, "
        "start_offset BIGINT, end_offset BIGINT, locator_type VARCHAR, scenario_id VARCHAR, locator VARCHAR, "
        "excerpt VARCHAR"
    ),
    "expected_queries": (
        "dataset_id VARCHAR, query_id VARCHAR, question VARCHAR, principal_id VARCHAR, required_structured VARCHAR, "
        "required_entities VARCHAR, required_artifacts VARCHAR, required_claims VARCHAR, kind VARCHAR, "
        "difficulty VARCHAR, required_evidence VARCHAR"
    ),
}


def _mention(mid, entity, artifact, surface, text, locator, tier, scenario):
    offsets = span(text, surface) if text is not None else (None, None)
    full = {**locator, "start": offsets[0], "end": offsets[1]} if text is not None else locator
    return (DATASET, mid, entity, artifact, surface, "text", offsets[0], offsets[1], scenario,
            TRUTH_ENTITIES[entity][0], json.dumps(full), tier)


TRUTH_MENTIONS = [
    _mention("tm1", "E_item", "a-email", "blanched fragrances", BODY, {"part": "body", "line_endings": "LF"}, "direct", "S1"),
    _mention("tm2", "E_ret", "a-email", "RMA-4853189", SUBJECT, {"part": "subject"}, "direct", "S1"),
    _mention("tm3", "E_cust", "a-email", "Wilma Graham", None, {"header": "From"}, "direct", "S1"),
    _mention("tm4", "E_cust", "b-chat", "Dr. Carlos", MSG1, {"message_id": "msg-1"}, "alias", "S1"),
    _mention("tm5", "E_sale", "b-chat", "receipt ending in 4309", MSG1, {"message_id": "msg-1"}, "alias", "S1"),
    _mention("tm6", "E_cust", "c-pdf", "AAAAAAAABIKFBAAA", None, {"page": 1, "text": "AAAAAAAABIKFBAAA"}, "direct", "S2"),
    _mention("tm7", "E_item", "b-chat", "the item", MSG2, {"message_id": "msg-2"}, "contextual", "S1"),
    _mention("tm8", "E_store", "a-email", "Midway store", BODY, {"part": "body", "line_endings": "LF"}, "alias", "S1"),
]
TRUTH_RELATIONSHIPS = [
    ("r1", "E_art_email", "MENTIONS", "E_item", "a-email"),
    ("r2", "E_art_email", "MENTIONS", "E_cust", "a-email"),
    ("r3", "E_art_email", "DISCUSSES", "E_ret", "a-email"),
    ("r4", "E_cust", "PURCHASED_IN", "E_sale", None),
    ("r5", "E_ret", "RETURNED_BY", "E_cust", None),
    ("r6", "E_sale", "CONTAINS", "E_item", None),
    ("r7", "E_ret", "RETURNS_ITEM", "E_item", None),
    ("r8", "E_ret", "RETURNED_AT", "E_store", None),
    ("r9", "E_sale", "OCCURRED_AT", "E_store", None),
    ("r10", "E_ret", "HAS_REASON", "E_reason", None),
    ("r11", "E_ret", "REFERS_TO_SALE", "E_sale", None),
]
TRUTH_CLAIMS = [
    ("TC1", "S1", "PACKAGING_DAMAGED", "E_item", "E_ret"),
    ("TC2", "S1", "RETURN_REASON", "E_ret", "E_reason"),
    ("TC3", "S1", "REFUND_REQUESTED", "E_cust", "E_ret"),
]
CRUSHED = span(BODY, "The box was completely crushed and the product is unusable.")
DROPPED = span(MSG2, "Packaging damage, looks like the item was dropped.")
TRUTH_EVIDENCE = [
    ("TE1", "TC1", "a-email", "body", *CRUSHED, "email_body_char_span",
     {"part": "body", "line_endings": "LF", "start": CRUSHED[0], "end": CRUSHED[1]}),
    ("TE2", "TC2", "c-pdf", "page-1", None, None, "pdf_page_text_span",
     {"page": 1, "text": "Recorded return reason: Package was damaged."}),
    ("TE3", "TC3", "b-chat", "msg-2", *DROPPED, "chat_message_char_span",
     {"message_id": "msg-2", "start": DROPPED[0], "end": DROPPED[1]}),
    ("TE4", "TC2", "b-chat", "msg-2", *DROPPED, "chat_message_char_span",
     {"message_id": "msg-2", "start": DROPPED[0], "end": DROPPED[1]}),
]
TRUTH_QUERIES = [
    ("Q1", "unstructured", None, ["E_item", "E_ret"], ["a-email"], ["TC1"], ["TE1"]),
    ("Q2", "resolution", None, ["E_sale"], ["b-chat"], [], []),
    ("Q3", "no_answer", {"key": {"i_item_sk": 1390}, "table": "item"}, [], [], [], []),
    ("Q4", "no_answer", {"key": {"i_item_sk": 2606}, "table": "item"}, [], [], [], []),
    ("Q5", "joined", None, ["E_cust"], [], ["TC3"], ["TE3"]),
]


def build_truth() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("CREATE SCHEMA helios_ground_truth")
    for table, columns in TRUTH_DDL.items():
        con.execute(f"CREATE TABLE helios_ground_truth.{table} ({columns})")
    con.executemany(
        "INSERT INTO helios_ground_truth.entities VALUES (?, ?, ?, ?, ?)",
        [(DATASET, eid, cls, json.dumps(key), eid) for eid, (cls, key) in TRUTH_ENTITIES.items()],
    )
    con.executemany(
        "INSERT INTO helios_ground_truth.entity_mentions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        TRUTH_MENTIONS,
    )
    con.executemany(
        "INSERT INTO helios_ground_truth.relationships VALUES (?, ?, ?, ?, ?, 1.0, 'S1', ?)",
        [(DATASET, *r) for r in TRUTH_RELATIONSHIPS],
    )
    con.executemany(
        "INSERT INTO helios_ground_truth.claims VALUES (?, ?, ?, ?, ?, ?, 'INTENDED_TRUE', '')",
        [(DATASET, *c) for c in TRUTH_CLAIMS],
    )
    con.executemany(
        "INSERT INTO helios_ground_truth.evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'S1', ?, ?)",
        [(DATASET, *e[:7], json.dumps(e[7]), e[7].get("text", "")) for e in TRUTH_EVIDENCE],
    )
    con.executemany(
        "INSERT INTO helios_ground_truth.expected_queries VALUES (?, ?, ?, 'analyst', ?, ?, ?, ?, ?, 'direct', ?)",
        [
            (DATASET, qid, f"question {qid}", json.dumps(structured) if structured else None,
             json.dumps(entities), json.dumps(artifacts), json.dumps(claims), kind, json.dumps(evidence))
            for qid, kind, structured, entities, artifacts, claims, evidence in TRUTH_QUERIES
        ],
    )
    return con


@pytest.fixture
def index():
    store = duckdb_index_store()
    store.ensure_tables()
    return store


@pytest.fixture
def run_id(index):
    return build_run(index)


@pytest.fixture
def truth():
    return build_truth()


def _clock():
    ticks = itertools.count()
    return lambda: f"2026-10-02T00:00:{next(ticks):02d}+00:00"


@pytest.fixture
def record(index, run_id, truth):
    return harness.evaluate(
        index, truth.cursor, run_id, DATASET, evaluator="alice", evaluator_mode="workload_user",
        clock=_clock(),
    )


# --- keys and loading --------------------------------------------------------------------


def test_truth_keys_follow_the_crawler_external_id_convention():
    assert KEY["E_cust"] == "tpcds.customer:c_customer_sk=96292"
    assert KEY["E_ret"] == "tpcds.store_returns:sr_item_sk=2606,sr_ticket_number=234309"
    assert KEY["E_art_pdf"] == "c-pdf"
    assert harness.truth_key({"source_key": {"i_brand": "exportinameless #1", "table": "item"}}) == (
        "tpcds.item:i_brand=exportinameless #1"
    )


def test_truth_is_loaded_per_dataset_with_json_decoded(truth):
    loaded = harness.load_truth(truth.cursor, DATASET)
    assert len(loaded.entity_mentions) == 8 and loaded.entity_mentions[0]["locator"]["part"] == "body"
    assert loaded.expected_queries[2]["required_structured"]["key"] == {"i_item_sk": 1390}
    assert harness.load_truth(truth.cursor, "other").entities == []


def test_a_ranger_denial_is_reported_as_such():
    class Denied:
        def execute(self, sql, params=None):
            raise RuntimeError("AuthorizationException: User 'bob' does not have privileges")

    with pytest.raises(harness.GroundTruthDenied):
        harness.load_truth(lambda: Denied(), DATASET)


# --- the metrics ---------------------------------------------------------------------------


def test_segment_coverage_is_complete_for_the_synthetic_run(record):
    segments = record.metrics["segments"]
    assert segments["coverage"] == 1.0
    assert (segments["evidence_in_segments"], segments["mentions_in_segments"]) == (4, 8)
    assert segments["page_text_coverage"] == 1.0


def test_segment_coverage_drops_for_locators_outside_the_segments(index, run_id):
    segments = index.read("helios_index.segments", {"crawl_run_id": run_id})
    outside = [
        {"artifact_id": "b-chat", "locator": {"message_id": "msg-9", "start": 0, "end": 3}},
        {"artifact_id": "a-email", "locator": {"part": "body", "start": 0, "end": 10_000}},
        {"artifact_id": "a-email", "locator": {"part": "subject", "start": 0, "end": 5}},
    ]
    scored = harness.score_segments(outside, [], segments)
    assert (scored["evidence_in_segments"], scored["evidence_coverage"]) == (1, 0.3333)


def test_mention_recall_and_precision_by_class_and_tier(record):
    mentions = record.metrics["mentions"]
    assert (mentions["truth_total"], mentions["truth_found"], mentions["recall"]) == (8, 6, 0.75)
    # cm7 ("unusable", no class) is a value mention: counted, not scored for precision.
    assert (mentions["crawler_total"], mentions["crawler_matched"], mentions["precision"]) == (6, 6, 1.0)
    assert mentions["value_mentions"] == 1
    assert mentions["by_tier"]["direct"]["recall"] == 1.0  # span overlap, header and page surfaces
    assert mentions["by_tier"]["alias"]["recall"] == 0.6667
    assert mentions["by_tier"]["contextual"]["recall"] == 0.0
    assert mentions["by_class_tier"]["Customer"]["direct"] == {
        "truth_total": 2, "truth_found": 2, "recall": 1.0, "class_correct": 2, "class_correct_rate": 1.0,
    }
    assert mentions["class_correct_rate"] == 1.0
    assert mentions["precision_by"]["extractor:pattern"]["precision"] == 1.0
    assert "class:?" not in mentions["precision_by"]
    assert record.summary["mention_recall_direct"] == 1.0
    assert record.summary["mention_precision"] == 1.0


def test_mention_diagnostics_name_what_was_missed_and_what_was_extra(record):
    diagnostics = record.metrics["diagnostics"]["mentions"]
    assert diagnostics["unmatched_crawler_mentions"] == []  # the value mention is not extra
    assert diagnostics["unfound_truth_surfaces"] == [
        {"class": "Item", "tier": "contextual", "surface": "the item", "count": 1},
        {"class": "Store", "tier": "alias", "surface": "midway store", "count": 1},
    ]


def test_mention_matching_rules():
    truth = [
        {"mention_id": "t1", "artifact_id": "a", "surface_form": "Midway", "locator": {"part": "body", "start": 10, "end": 16}},
        {"mention_id": "t2", "artifact_id": "a", "surface_form": "Wilma Graham", "locator": {"header": "From"}},
        {"mention_id": "t3", "artifact_id": "a", "surface_form": "AB", "locator": {"page": 1, "text": "AB"}},
    ]

    def m(mid, surface, locator):
        return MentionRecord(
            crawl_run_id="r", mention_id=mid, asset_id="a", segment_id="s", surface_form=surface,
            locator=locator, extractor="pattern",
        )

    crawler = [
        m("c1", "your Midway store", {"part": "body", "start": 5, "end": 22}),  # overlaps t1
        m("c2", "Midway", {"part": "subject", "start": 10, "end": 16}),  # wrong part
        m("c3", "Wilma Graham <w@t.edu>", {"header": "From"}),  # contains t2 (>= 5 chars)
        m("c4", "ABCDE", {"page": 1, "text": "ABCDE"}),  # t3 is too short for containment
        m("c5", "ab", {"page": 1, "text": "ab"}),  # case-insensitive equality
    ]
    matches = harness.match_mentions(truth, crawler)
    assert matches.found == {"t1": ["c1"], "t2": ["c3"], "t3": ["c5"]}
    assert set(matches.matched) == {"c1", "c3", "c5"}


def test_resolution_accuracy_by_tier_and_wrong_sameas_links(record):
    resolution = record.metrics["resolution"]
    assert resolution["by_tier"]["direct"] == {
        "found": 4, "correct": 3, "wrong": 1, "possibly_only": 0, "unresolved": 0, "accuracy": 0.75,
    }
    assert resolution["by_tier"]["alias"] == {
        "found": 2, "correct": 1, "wrong": 0, "possibly_only": 1, "unresolved": 0, "accuracy": 0.5,
    }
    assert resolution["by_resolver"]["fuzzy"] == {"links": 1, "correct": 0, "accuracy": 0.0}
    assert resolution["by_resolver"]["exact_key"] == {"links": 3, "correct": 3, "accuracy": 1.0}
    assert (resolution["sameas_links"], resolution["sameas_correct"], resolution["sameas_wrong"]) == (5, 4, 1)
    assert resolution["sameas_precision"] == 0.8
    assert resolution["possibly_sameas_links"] == 1
    assert record.summary["resolution_accuracy_alias"] == 0.5
    assert record.summary["sameas_precision"] == 0.8


def test_cases_are_compared_pairwise_with_the_hidden_scenarios(record, index, run_id):
    cases = record.metrics["cases"]
    # About links all three assets to one Return; the truth has {a-email, b-chat} and {c-pdf}.
    assert cases == {
        "skipped": False, "grouping": "About", "assets_grouped": 3, "crawler_pairs": 3,
        "truth_pairs": 1, "pairs_correct": 1, "precision": 0.3333, "recall": 1.0, "f1": 0.5,
    }
    assert harness.score_cases([], [], [], [], [])["skipped"] is True


def test_relationships_are_mapped_onto_the_ontology_including_the_reversed_purchase(record):
    relationships = record.metrics["relationships"]
    assert relationships["by_type"]["PartyTo"] == {
        "truth_total": 2, "truth_found": 2, "recall": 1.0, "crawler_total": 2, "crawler_matched": 2,
        "precision": 1.0, "f1": 1.0,
    }
    assert relationships["by_predicate"]["PURCHASED_IN"]["recall"] == 1.0
    assert relationships["by_type"]["Contains"]["truth_found"] == 1  # CONTAINS yes, RETURNS_ITEM no
    assert relationships["by_type"]["Mentions"] == {
        "truth_total": 2, "truth_found": 1, "recall": 0.5, "crawler_total": 1, "crawler_matched": 1,
        "precision": 1.0, "f1": 0.6667,
    }
    assert relationships["by_predicate"]["REFERS_TO_SALE"]["recall"] == 0.0
    assert (relationships["truth_total"], relationships["truth_found"]) == (11, 7)


def test_claims_and_evidence_agreement(record):
    claims = record.metrics["claims"]
    assert (claims["truth_total"], claims["truth_found"], claims["recall"]) == (3, 2, 0.6667)
    assert (claims["crawler_total"], claims["crawler_matched"], claims["precision"]) == (3, 2, 0.6667)
    assert claims["by_predicate"]["REFUND_APPROVED"]["crawler_matched"] == 0
    evidence = record.metrics["evidence"]
    # TC1: body span overlap; TC2: page text containment (TE2), its chat evidence TE4 unmatched.
    assert evidence["claims_with_agreeing_evidence"] == 2 and evidence["agreement"] == 1.0
    assert (evidence["evidence_rows_of_matched_claims"], evidence["evidence_rows_matched"]) == (3, 2)
    assert record.summary["claim_recall"] == 0.6667
    assert record.summary["evidence_agreement"] == 1.0


def test_evidence_disagrees_when_the_part_or_span_differs():
    truth = [
        {"evidence_id": "e1", "claim_id": "t", "artifact_id": "a", "locator": {"part": "body", "start": 0, "end": 10}},
    ]
    crawler = [
        ClaimEvidenceRecord(crawl_run_id="r", claim_id="c", evidence_id="x", asset_id="a", segment_id="s",
                            locator={"part": "body", "start": 20, "end": 30}, excerpt="later"),
    ]
    scored = harness.score_evidence(truth, {"t": ["c"]}, crawler)
    assert scored["agreement"] == 0.0 and scored["evidence_rows_matched"] == 0


def test_golden_questions_at_the_retrieval_level_including_no_answer(record):
    questions = record.metrics["questions"]
    by_id = {r["query_id"]: r for r in questions["results"]}
    assert by_id["Q1"]["passed"] and by_id["Q1"]["checks"] == {
        "entities_linked": True, "artifacts_indexed": True, "claims_found": True,
        "evidence_segmented": True, "evidence_indexed": True,
    }
    assert not by_id["Q2"]["passed"] and by_id["Q2"]["checks"]["entities_linked"] is False  # only PossiblySameAs
    assert by_id["Q3"]["passed"]  # nothing links or mentions item 1390
    assert not by_id["Q4"]["passed"] and by_id["Q4"]["checks"] == {"no_links": False, "no_mentions": False}
    assert not by_id["Q5"]["passed"] and by_id["Q5"]["checks"]["claims_found"] is False
    assert questions["by_kind"]["no_answer"] == {"total": 2, "passed": 1, "pass_rate": 0.5}
    assert record.summary["questions_pass_rate"] == 0.4


# --- the record ----------------------------------------------------------------------------


def test_the_record_carries_the_run_context_and_is_written_to_the_index(record, index, run_id):
    assert record.status == "SUCCEEDED" and record.error is None
    assert (record.evaluator, record.evaluator_mode) == ("alice", "workload_user")
    assert (record.strategy, record.ontology_version, record.harness_version) == ("deterministic", ONT, "0.1.0")
    assert record.metrics["run"]["truth"] == {
        "entities": 10, "mentions": 8, "relationships": 11, "claims": 3, "evidence": 4, "questions": 5,
    }
    [stored] = harness.evaluations(index, crawl_run_id=run_id)
    assert stored == record
    assert harness.evaluations(index, dataset_id="other") == []


def test_re_evaluation_is_reproducible_and_readers_take_the_latest(index, run_id, truth):
    clock = _clock()
    first = harness.evaluate(index, truth.cursor, run_id, DATASET, evaluator="alice", evaluator_mode="proxy", clock=clock)
    second = harness.evaluate(index, truth.cursor, run_id, DATASET, evaluator="bob", evaluator_mode="proxy", clock=clock)
    assert first.evaluation_id == second.evaluation_id
    assert json.dumps(first.metrics, sort_keys=True) == json.dumps(second.metrics, sort_keys=True)
    assert first.summary == second.summary
    assert len(index.read("helios_index.evaluations", {"crawl_run_id": run_id})) == 2
    [latest] = harness.evaluations(index, crawl_run_id=run_id)
    assert latest.evaluator == "bob"


def test_unknown_runs_and_failures(index, run_id, truth):
    with pytest.raises(harness.UnknownRun):
        harness.evaluate(index, truth.cursor, "crawl_nope", DATASET, evaluator="a", evaluator_mode="proxy")

    def broken():
        raise RuntimeError("Impala went away")

    with pytest.raises(harness.EvaluationFailed) as info:
        harness.evaluate(index, broken, run_id, DATASET, evaluator="a", evaluator_mode="proxy")
    assert info.value.record.status == "FAILED" and "Impala went away" in info.value.record.error
    [stored] = harness.evaluations(index, crawl_run_id=run_id)
    assert stored.status == "FAILED"


# --- the API ---------------------------------------------------------------------------------


@pytest.fixture
def client(index, monkeypatch):
    from apps.helios.console import crawler, ontology
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Patch the accessor, not just the cache: importing the console app starts a
    # background cache restore that may rebuild the cached store mid-test.
    monkeypatch.setitem(ontology._index, "store", index)
    monkeypatch.setattr(ontology, "index_store", lambda: index)
    app = FastAPI()
    app.include_router(crawler.crawler_router)
    return TestClient(app)


ALICE = {"x-forwarded-user": "alice"}


def test_evaluate_requires_a_principal(client, run_id):
    response = client.post(f"/api/v1/crawler/runs/{run_id}:evaluate", json={"dataset_id": DATASET})
    assert response.status_code == 401


def test_evaluate_refuses_a_principal_ranger_denies(client, run_id, monkeypatch):
    from apps.helios.console import crawler

    class DeniedCursor:
        def execute(self, sql, params=None):
            raise RuntimeError(
                "AuthorizationException: User 'alice' does not have privileges to execute "
                "'SELECT' on: helios_ground_truth.entities"
            )

    class Connection:
        def cursor(self):
            return DeniedCursor()

        def close(self):
            pass

    monkeypatch.setattr(crawler, "_truth_connection", lambda principal_id: (Connection(), "proxy"))
    response = client.post(
        f"/api/v1/crawler/runs/{run_id}:evaluate", json={"dataset_id": DATASET}, headers=ALICE
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "ground truth is not readable by cloudera-workbench:alice"
    assert client.get(f"/api/v1/crawler/runs/{run_id}/evaluations").json() == []


def test_evaluate_scores_the_run_and_the_views_show_it(client, run_id, truth, monkeypatch):
    from apps.helios.console import crawler

    monkeypatch.setattr(crawler, "_truth_connection", lambda principal_id: (truth, "proxy"))
    response = client.post(
        f"/api/v1/crawler/runs/{run_id}:evaluate", json={"dataset_id": DATASET}, headers=ALICE
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["evaluator"], body["evaluator_mode"], body["status"]) == ("cloudera-workbench:alice", "proxy", "SUCCEEDED")
    assert body["summary"]["segments_coverage"] == 1.0
    assert body["metrics"]["mentions"]["recall"] == 0.75
    assert body["run"]["crawler_version"] == "0.3.0"

    [for_run] = client.get(f"/api/v1/crawler/runs/{run_id}/evaluations").json()
    assert for_run["evaluation_id"] == body["evaluation_id"]
    [for_dataset] = client.get(f"/api/v1/crawler/evaluations?dataset={DATASET}").json()
    assert for_dataset["run"]["started_at"] and for_dataset["strategy"] == "deterministic"
    assert client.get("/api/v1/crawler/evaluations?dataset=other").json() == []

    [run_view] = client.get("/api/v1/crawler/runs").json()
    assert run_view["latest_evaluation"]["summary"]["mention_recall_direct"] == 1.0
    assert "metrics" not in run_view["latest_evaluation"]
    assert client.get(f"/api/v1/crawler/runs/{run_id}").json()["latest_evaluation"]["dataset_id"] == DATASET

    missing = client.post("/api/v1/crawler/runs/crawl_nope:evaluate", json={"dataset_id": DATASET}, headers=ALICE)
    assert missing.status_code == 404


# --- the CLI ---------------------------------------------------------------------------------


def test_cli_scores_a_duckdb_run_against_impala_truth(tmp_path, truth, monkeypatch, capsys):
    import helios_core.config as config_module
    import helios_core.engines.impala as impala_module
    from apps.helios.crawler.__main__ import main

    path = str(tmp_path / "index.duckdb")
    store = duckdb_index_store(path)
    store.ensure_tables()
    run_id = build_run(store)
    store._connection().close()

    class FakeEngine:
        def __init__(self, cfg):
            self.cfg = cfg

        def connect(self):
            return truth

    monkeypatch.setattr(
        config_module, "impala_config", lambda: config_module.ImpalaConfig("h", 443, "chris", "pw")
    )
    monkeypatch.setattr(impala_module, "ImpalaEngine", FakeEngine)
    assert main(["evaluate", "--run", run_id, "--dataset", DATASET, "--index-duckdb", path]) == 0
    out = capsys.readouterr().out
    assert "segments_coverage" in out and "1.0000" in out and "(chris, workload_user)" in out
    reopened = duckdb_index_store(path)
    [stored] = harness.evaluations(reopened, crawl_run_id=run_id)
    assert stored.summary["mention_recall_direct"] == 1.0

    assert main(["evaluate", "--run", "crawl_nope", "--dataset", DATASET, "--index-duckdb", path]) == 2
