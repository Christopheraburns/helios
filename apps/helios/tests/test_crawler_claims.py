"""CR-6: claim extraction over resolved cases: units with exact offsets, cue
lexicons with negation and hedge guards, subjects and objects from the case's
resolved entities, one claim per predicate per case with evidence across its
documents, and the crawl writing claims and evidence."""

import hashlib
import json
from types import SimpleNamespace

import duckdb
import pytest
import test_crawler_resolution as resolution_fixtures
from apps.helios.crawler.analyzers import analyze_asset
from apps.helios.crawler.claims import (
    SHAPES,
    compile_cue,
    cue_strength,
    extract_claims,
    find_hits,
    sentence_spans,
    split_units,
)
from apps.helios.crawler.connectors import SourceAsset
from apps.helios.crawler.crawl import segment_rows
from apps.helios.crawler.mentions import extract_mentions
from apps.helios.crawler.resolution import resolve
from helios_core.crawler.settings import DEFAULT_SETTINGS
from helios_core.index import ids
from helios_core.index.records import SegmentRecord

CONFIG = resolution_fixtures.CONFIG
RETURN_A, RETURN_B = resolution_fixtures.RETURN_A, resolution_fixtures.RETURN_B
CUSTOMER, ITEM, REASON = (
    resolution_fixtures.CUSTOMER,
    resolution_fixtures.ITEM,
    resolution_fixtures.REASON,
)


def chat(*messages: tuple[str, str]) -> bytes:
    """A chat thread from (role, text) pairs."""
    roles = sorted({role for role, _ in messages})
    return json.dumps(
        {
            "schema": "helios-ds/chat-thread/1.0",
            "thread_id": "thread-x",
            "channel": "support",
            "participants": [{"sender": f"{r}_1", "name": r.title(), "role": r} for r in roles],
            "messages": [
                {
                    "message_id": f"msg-{i}",
                    "timestamp": "1999-05-05T10:00:00Z",
                    "sender": f"{role}_1",
                    "sender_name": role.title(),
                    "text": text,
                }
                for i, (role, text) in enumerate(messages, start=1)
            ],
        }
    ).encode()


def read(run, gazetteer, documents: dict[str, tuple[str, bytes]]):
    assets, segments, mentions = [], [], []
    for asset_id, (mime, data) in documents.items():
        asset = SourceAsset(
            asset_id=asset_id, source="ds-1", mime_type=mime, locator={}, version=f"v-{asset_id}"
        )
        analysis = analyze_asset(mime, data, DEFAULT_SETTINGS)
        assert analysis.status == "analyzed", analysis.detail
        own = segment_rows(run, asset, analysis.segments)
        assets.append(resolution_fixtures._asset_record(run, asset))
        segments += own
        mentions += extract_mentions(run, asset, own, DEFAULT_SETTINGS, gazetteer, CONFIG)
    return assets, segments, mentions


def extract(warehouse, gazetteer, documents, run_id="run-1"):
    run = resolution_fixtures._run(run_id)
    assets, segments, mentions = read(run, gazetteer, documents)
    resolved = resolve(
        run, assets, segments, mentions, gazetteer, CONFIG, DEFAULT_SETTINGS, warehouse.cursor
    )
    extraction = extract_claims(
        run,
        resolved.clusters,
        assets,
        segments,
        mentions,
        resolved.links,
        resolved.entities,
        resolved.relationships,
        DEFAULT_SETTINGS,
    )
    keys = {e.entity_id: e.external_ids[0] for e in resolved.entities}
    return SimpleNamespace(
        run=run,
        segments={s.segment_id: s for s in segments},
        resolved=resolved,
        extraction=extraction,
        claims={
            (c.predicate, keys[c.subject_entity_id], keys[c.object_entity_id]): c
            for c in extraction.claims
        },
        evidence_of=lambda claim: [e for e in extraction.evidence if e.claim_id == claim.claim_id],
    )


@pytest.fixture(scope="module")
def warehouse():
    return resolution_fixtures.build_warehouse()


@pytest.fixture(scope="module")
def gazetteer(warehouse):
    return resolution_fixtures.Gazetteer.build(
        warehouse.cursor, CONFIG, resolution_fixtures.CLASSES
    )


@pytest.fixture(scope="module")
def world(warehouse, gazetteer):
    return extract(warehouse, gazetteer, resolution_fixtures.ASSETS)


# --- units ----------------------------------------------------------------------------------


def test_sentences_keep_exact_offsets_and_do_not_split_abbreviations():
    text = (
        "Hello,\n\nMrs. Raymond brought it back. Box arrived torn; the product scratched. "
        "The return slip says: Package was damaged.\nInspected by Arjun V., returns desk.\n"
        "Disposition: refund of $4.85 issued; item quarantined.\n"
    )
    sentences = [text[s:e] for s, e in sentence_spans(text)]
    assert sentences == [
        "Hello,",
        "Mrs. Raymond brought it back.",
        "Box arrived torn; the product scratched.",
        "The return slip says: Package was damaged.",
        "Inspected by Arjun V., returns desk.",
        "Disposition: refund of $4.85 issued; item quarantined.",
    ]
    assert all(s == s.strip() for s in sentences)


def _segment(segment_type, text, locator=None, structure=None):
    return SegmentRecord(
        crawl_run_id="run-1",
        segment_id="seg-1",
        asset_id="asset-1",
        segment_type=segment_type,
        ordinal=0,
        locator=locator or {},
        text=text,
        structure=structure or {},
    )


def test_a_chat_message_is_one_unit_and_headers_are_not():
    message = _segment(
        "message", "  Yep, I logged that one. Box was crushed. ", {"message_id": "m"}
    )
    [unit] = split_units(message)
    assert unit.text == "Yep, I logged that one. Box was crushed." and (unit.start, unit.end) == (
        2,
        42,
    )
    assert split_units(_segment("email_header", "Wilma <w@t.edu>", {"header": "From"})) == []
    assert split_units(_segment("email_subject", "Damaged item", {"part": "subject"})) == []


# --- cues and guards -----------------------------------------------------------------------------


def hits(text):
    return {p: h.cue.phrase for p, h in find_hits(text, _cues(), DEFAULT_SETTINGS).items()}


def _cues():
    from apps.helios.crawler.claims import compile_cues

    return compile_cues(DEFAULT_SETTINGS)


def test_cues_match_whole_words_case_insensitively_with_gaps():
    assert compile_cue("torn").search("the carton was Torn open") is not None
    assert compile_cue("torn").search("a stubborn customer") is None
    assert compile_cue("refund * approved").search("full refund of $310.40 approved;") is not None
    assert compile_cue("refund * approved").search("refund approved") is not None
    assert compile_cue("disposition:").search("Disposition: refund issued") is not None
    assert cue_strength("reason code on file") == "strong"
    assert cue_strength("disposition:") == "strong"
    assert cue_strength("approve it") == "medium"
    assert cue_strength("crushed") == "medium"
    assert cue_strength("damage") == "weak"


def test_negations_and_hedges_block_a_cue_in_their_clause():
    assert "PACKAGING_DAMAGED" not in hits("The box was not damaged.")
    assert "PACKAGING_DAMAGED" not in hits("There was no damage to the item.")
    assert "REFUND_APPROVED" not in hits("Let me know if the refund is approved.")
    assert "REFUND_APPROVED" not in hits("I wonder whether it was approved.")
    assert "REFUND_APPROVED" not in hits("The refund wasn't approved.")
    assert "PACKAGING_DAMAGED" in hits("The box was damaged.")
    # The negation applies to "the customer", in another clause: the approval stands.
    found = hits("Damage is on the carrier, not the customer. Approve it.")
    assert found["REFUND_APPROVED"] == "approve it" and "PACKAGING_DAMAGED" in found


# --- claims on the worked example -----------------------------------------------------------------


def _excerpts(world, claim):
    rows = world.evidence_of(claim)
    for row in rows:
        segment = world.segments[row.segment_id]
        assert segment.text[row.locator["start"] : row.locator["end"]] == row.excerpt
    return sorted((r.asset_id, r.excerpt) for r in rows)


def test_the_worked_example_yields_all_four_predicates(world):
    damaged = world.claims[("PACKAGING_DAMAGED", ITEM[11], RETURN_A)]
    assert _excerpts(world, damaged) == [("a-email", "Box arrived torn; the product scratched.")]
    reason = world.claims[("RETURN_REASON", RETURN_A, REASON[1])]
    assert _excerpts(world, reason) == [
        ("a-email", "The return slip says: Package was damaged."),
        ("a-pdf", "Recorded return reason: Package was damaged."),
    ]
    requested = world.claims[("REFUND_REQUESTED", CUSTOMER[2], RETURN_A)]
    assert _excerpts(world, requested) == [("a-email", "When will the refund post?")]
    approved = world.claims[("REFUND_APPROVED", RETURN_A, CUSTOMER[2])]
    [(asset, excerpt)] = _excerpts(world, approved)
    assert asset == "a-chat" and excerpt.endswith("refund of $42.00 approved.")
    assert all(c.extractor == "cues" and c.object_value is None for c in world.claims.values())
    assert reason.confidence == 0.95 and damaged.confidence == 0.85
    assert {c.predicate for c in world.claims.values()} == set(SHAPES)


def test_evidence_locators_follow_the_segment_conventions(world):
    reason = world.claims[("RETURN_REASON", RETURN_A, REASON[1])]
    by_asset = {e.asset_id: e for e in world.evidence_of(reason)}
    assert by_asset["a-pdf"].locator == {
        "page": 1,
        "start": by_asset["a-pdf"].locator["start"],
        "end": by_asset["a-pdf"].locator["end"],
        "text": "Recorded return reason: Package was damaged.",
    }
    assert set(by_asset["a-email"].locator) == {"part", "start", "end"}
    assert by_asset["a-email"].locator["part"] == "body"
    approved = world.claims[("REFUND_APPROVED", RETURN_A, CUSTOMER[2])]
    [row] = world.evidence_of(approved)
    assert row.locator == {"message_id": "msg-2", "start": 0, "end": len(row.excerpt)}


def test_one_claim_per_predicate_per_case_with_evidence_from_several_documents(world):
    damaged = world.claims[("PACKAGING_DAMAGED", ITEM[10], RETURN_B)]
    assert _excerpts(world, damaged) == [
        ("b-chat", "Box was crushed, the item has dents."),
        ("b-pdf", "Outer packaging crushed on two corners."),
    ]
    assert sum(1 for c in world.claims if c[0] == "PACKAGING_DAMAGED" and c[2] == RETURN_B) == 1
    assert len({c.claim_id for c in world.extraction.claims}) == len(world.extraction.claims)
    assert len({e.evidence_id for e in world.extraction.evidence}) == len(world.extraction.evidence)
    assert world.extraction.counts == {
        "claims": len(world.extraction.claims),
        "claim_evidence": len(world.extraction.evidence),
        "claims_unanchored": world.extraction.unanchored,
    }


def test_a_cue_with_no_resolved_subject_yields_nothing(world, warehouse, gazetteer):
    # d-chat: ticket 999999 exists nowhere, two possible items, no Return: no claims.
    assert not [c for c in world.claims if world.claims[c].crawl_run_id and c[2].endswith("999999")]
    alone = extract(
        warehouse,
        gazetteer,
        {
            "x-chat": (
                "application/json",
                chat(("inspector", "Box was crushed, the item has dents.")),
            )
        },
    )
    assert alone.extraction.claims == [] and alone.extraction.evidence == []
    assert alone.extraction.unanchored == 1


def test_ids_are_stable_across_runs(world, warehouse, gazetteer):
    again = extract(warehouse, gazetteer, resolution_fixtures.ASSETS, run_id="run-2")
    assert [c.claim_id for c in again.extraction.claims] == [
        c.claim_id for c in world.extraction.claims
    ]
    assert [e.evidence_id for e in again.extraction.evidence] == [
        e.evidence_id for e in world.extraction.evidence
    ]
    assert all(c.crawl_run_id == "run-2" for c in again.extraction.claims)
    claim = world.claims[("RETURN_REASON", RETURN_A, REASON[1])]
    assert claim.claim_id == ids.claim_id(
        "RETURN_REASON", claim.subject_entity_id, claim.object_entity_id
    )


# --- guards and voices in a whole case --------------------------------------------------------


def test_negated_and_hedged_sentences_and_customer_approvals_make_no_claims(warehouse, gazetteer):
    documents = {
        "n-email": (
            "message/rfc822",
            resolution_fixtures.email(
                "Wilma Graham <Wilma.Graham@t.edu>",
                "My ableeseantiantiought",
                "Re: blanched fragrances item (AAAAAAAAGLMDAAAA), receipt ending in 5079.\n"
                "The box was not damaged and there was no damage to the product.\n"
                "If the refund is approved, let me know. I expect a full refund of $42.00.\n"
                "Return # RMA-3056773.\n",
            ),
        ),
        "n-chat": (
            "application/json",
            chat(
                ("agent", "Got an email about return RMA-3056773 from Wilma.Graham@t.edu."),
                ("supervisor", "Damage is on the carrier, not the customer. Approve it."),
            ),
        ),
    }
    case = extract(warehouse, gazetteer, documents)
    assert case.resolved.resolved == 1
    # "not damaged" and "no damage" are guarded; the supervisor's "Damage is on
    # the carrier" is a generic word with no item named, so no damage claim.
    predicates = sorted(c[0] for c in case.claims)
    assert predicates == ["REFUND_APPROVED", "REFUND_REQUESTED"]
    # The customer's "full refund of" is a request, never an approval, and the
    # hedged "If the refund is approved" is nothing; the supervisor's "Approve
    # it." is the approval despite the negation in the sentence before.
    requested = case.claims[("REFUND_REQUESTED", CUSTOMER[2], RETURN_A)]
    assert [e.excerpt for e in case.evidence_of(requested)] == ["I expect a full refund of $42.00."]
    approved = case.claims[("REFUND_APPROVED", RETURN_A, CUSTOMER[2])]
    assert [(e.asset_id, e.excerpt) for e in case.evidence_of(approved)] == [
        ("n-chat", "Damage is on the carrier, not the customer. Approve it.")
    ]
    assert case.extraction.unanchored >= 1


def test_an_unknown_author_needs_a_strong_cue_to_approve(warehouse, gazetteer):
    documents = {
        "u-email": (
            "message/rfc822",
            resolution_fixtures.email(
                "Someone <nobody@example.org>",
                "Return RMA-3056773",
                "Could you let me know when to expect the refund of $42.00 for this return?\n"
                "Return # RMA-3056773, item AAAAAAAAGLMDAAAA, receipt ending in 5079, "
                "Wilma.Graham@t.edu.\n",
            ),
        ),
    }
    case = extract(warehouse, gazetteer, documents)
    assert case.resolved.resolved == 1
    assert sorted(c[0] for c in case.claims) == ["REFUND_REQUESTED"]


def test_a_generic_word_counts_when_the_unit_names_the_subject(warehouse, gazetteer):
    documents = {
        "w-email": (
            "message/rfc822",
            resolution_fixtures.email(
                "Wilma Graham <Wilma.Graham@t.edu>",
                "My ableeseantiantiought",
                "Re: blanched fragrances item (AAAAAAAAGLMDAAAA), receipt ending in 5079.\n"
                "It came in a package that had clearly been damaged, and the item was broken "
                "as a result. The return slip says: Package was damaged.\nReturn # RMA-3056773.\n",
            ),
        ),
    }
    case = extract(warehouse, gazetteer, documents)
    damaged = case.claims[("PACKAGING_DAMAGED", ITEM[11], RETURN_A)]
    assert damaged.confidence == 0.7
    assert [e.excerpt for e in case.evidence_of(damaged)] == [
        "It came in a package that had clearly been damaged, and the item was broken as a result."
    ]
    assert ("RETURN_REASON", RETURN_A, REASON[1]) in case.claims


# --- in the crawl ---------------------------------------------------------------------------------


def test_crawl_writes_claims_and_evidence(warehouse, gazetteer, tmp_path):
    from apps.helios.crawler.connectors import HeliosDsConnector, object_reader
    from apps.helios.crawler.crawl import CRAWLER_VERSION, crawl
    from helios_core.index.store import duckdb_index_store

    assert CRAWLER_VERSION == "0.6.0"
    root = tmp_path / "objects"
    con = duckdb.connect()
    con.execute("CREATE SCHEMA helios_ds")
    con.execute(
        "CREATE TABLE helios_ds.crawlable_artifacts (artifact_id VARCHAR, dataset_id VARCHAR, "
        "artifact_type VARCHAR, mime_type VARCHAR, source_locator VARCHAR, sha256 VARCHAR, "
        "size_bytes BIGINT, semantic_timestamp VARCHAR)"
    )
    kinds = {"message/rfc822": "email", "application/json": "chat", "application/pdf": "pdf"}
    for artifact_id, (mime, data) in resolution_fixtures.ASSETS.items():
        key = f"datasets/ds-1/artifacts/{artifact_id}"
        (root / key).parent.mkdir(parents=True, exist_ok=True)
        (root / key).write_bytes(data)
        locator = {"connector_type": "helios_ds_file", "root": str(root), "key": key}
        con.execute(
            "INSERT INTO helios_ds.crawlable_artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                artifact_id,
                "ds-1",
                kinds[mime],
                mime,
                json.dumps(locator),
                hashlib.sha256(data).hexdigest(),
                len(data),
                None,
            ],
        )
    connector = HeliosDsConnector("ds-1", {"dataset_id": "ds-1"}, con.cursor, object_reader())
    index = duckdb_index_store()
    index.ensure_tables()

    def run_crawl(**overrides):
        return crawl(
            index,
            connector,
            "ds-1",
            actor="test",
            ontology_version="0.2.0",
            settings=None,
            settings_hash=DEFAULT_SETTINGS.content_hash(),
            crawler_settings=DEFAULT_SETTINGS,
            **{
                "gazetteer": gazetteer,
                "resolution": CONFIG,
                "warehouse_cursor": warehouse.cursor,
                **overrides,
            },
        )

    first = run_crawl()
    assert first.status == "SUCCEEDED"
    claims = index.read("helios_index.claims", {"crawl_run_id": first.crawl_run_id})
    evidence = index.read("helios_index.claim_evidence", {"crawl_run_id": first.crawl_run_id})
    assert first.counts["claims"] == len(claims) == 5
    assert first.counts["claim_evidence"] == len(evidence) == 7
    assert first.counts["claims_unanchored"] >= 1
    assert {c.predicate for c in claims} == set(SHAPES)
    assert {e.claim_id for e in evidence} == {c.claim_id for c in claims}
    assert all(
        c.ontology_version == "0.2.0" and c.crawl_run_id == first.crawl_run_id for c in claims
    )

    second = run_crawl()  # carried forward assets: claims recomputed, same IDs
    again = index.read("helios_index.claims", {"crawl_run_id": second.crawl_run_id})
    assert second.counts["carried_forward"] == 7
    assert sorted(c.claim_id for c in again) == sorted(c.claim_id for c in claims)

    plain = run_crawl(gazetteer=None, resolution=None, warehouse_cursor=None)
    assert plain.status == "SUCCEEDED" and "claims" not in plain.counts
    assert index.read("helios_index.claims", {"crawl_run_id": plain.crawl_run_id}) == []


def test_default_cue_lexicons_name_only_retail_predicates():
    from helios_core.crawler.settings import ontology_problems

    classes = {"Customer", "Item", "Brand", "Store", "Reason", "Sale", "Return"}
    assert ontology_problems(DEFAULT_SETTINGS, classes, set(SHAPES)) == []
    assert set(DEFAULT_SETTINGS.claims.cues) == set(SHAPES)
