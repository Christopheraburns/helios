"""Arm B, the LLM crawler (CR-L1), and the strategy recorded on a run (CR-E2)."""

import hashlib
import json

import duckdb
import pytest
from apps.helios.crawler import llm_arm
from apps.helios.crawler.crawl import crawl, previous_run
from apps.helios.crawler.llm_arm import LlmExtractor, Vocabulary, load_vocabulary, run_arm
from helios_core.crawler.settings import DEFAULT_SETTINGS
from helios_core.index import evidence
from helios_core.index.store import duckdb_index_store
from helios_core.llm.client import LLMError
from test_crawler_resolution import (  # noqa: F401 - fixtures
    ASSETS,
    CLASSES,
    CONFIG,
    REPO_ROOT,
    _run,
    gazetteer,
    read_assets,
    warehouse,
)

VOCABULARY = load_vocabulary(REPO_ROOT / "ontology", CLASSES, list(DEFAULT_SETTINGS.claims.cues))
EMAIL_REPLY = {
    "entities": [
        {"id": "e1", "segment": 0, "quote": "Wilma.Graham@t.edu", "class": "Customer"},
        {"id": "e2", "segment": 2, "quote": "RMA-3056773", "class": "Return"},
        {"id": "e3", "segment": 2, "quote": "Wilma", "class": "Customer"},
        {"id": "e4", "segment": 2, "quote": "Mr. Nobody of Nowhere", "class": "Customer"},
        {"id": "e5", "segment": 2, "quote": "refund", "class": "Refund"},
        {"id": "e6", "segment": 0, "quote": "AAAAAAAAGLMDAAAA", "class": "Item"},
    ],
    "claims": [
        {"predicate": "REFUND_REQUESTED", "segment": 2, "quote": "When will the refund post?", "subject": "e1", "object": "e2"},
        {"predicate": "REFUND_REQUESTED", "segment": 2, "quote": "Please refund me today.", "subject": "e1", "object": "e2"},
        {"predicate": "REFUND_REQUESTED", "segment": 2, "quote": "When will the refund post?", "subject": "e3", "object": "e2"},
        {"predicate": "REFUND_APPROVED", "segment": 2, "quote": "When will the refund post?", "subject": "e1", "object": "e2"},
        {"predicate": "REFUND_REQUESTED", "segment": 2, "quote": "When will the refund post?", "subject": "e4", "object": "e2"},
    ],
}


class FakeLlm:
    provider, model, temperature = "fake", "fake-1", 0.0
    api_key = "never-recorded"

    def __init__(self, failures=0):
        self.calls = []
        self.failures = failures
        self.timeout = 30.0

    def complete_with_usage(self, system, user):
        if self.failures:
            self.failures -= 1
            raise LLMError('fake 429: {"message":"Rate limit exceeded"}')
        self.calls.append((system, user))
        reply = EMAIL_REPLY if "Wilma.Graham@t.edu>" in user else {"entities": [], "claims": []}
        return "```json\n" + json.dumps(reply) + "\n```", 100, 20


def extractor(tmp_path, llm=None, sleep=None):
    return LlmExtractor(
        llm or FakeLlm(), DEFAULT_SETTINGS, VOCABULARY, tmp_path / "cache", sleep or (lambda s: None)
    )


def test_the_prompt_carries_the_ontology_and_never_the_answer_key():
    assert set(VOCABULARY.classes) == set(CLASSES)
    assert VOCABULARY.classes["Return"] == "The return of an item from an earlier sale."
    assert VOCABULARY.predicates["REFUND_REQUESTED"] == ("A customer asked for a refund.", "Customer", "Return")
    prompt = llm_arm.system_prompt(DEFAULT_SETTINGS, VOCABULARY)
    assert "- Reason: A coded reason" in prompt
    assert "- PACKAGING_DAMAGED (subject: Item, object: Return)" in prompt
    assert DEFAULT_SETTINGS.llm.instructions.splitlines()[0] in prompt
    changed = DEFAULT_SETTINGS.model_copy(
        update={"llm": DEFAULT_SETTINGS.llm.model_copy(update={"instructions": "Different."})}
    )
    assert llm_arm.system_prompt(changed, VOCABULARY) != prompt  # so the prompt hash changes


def test_quotes_are_grounded_and_resolved_by_exact_keys_only(gazetteer, tmp_path):  # noqa: F811
    run = _run()
    assets, segments, _ = read_assets(run, gazetteer, {"a-email", "a-chat"})
    llm = extractor(tmp_path)
    result = run_arm(run, assets, segments, llm, gazetteer, CONFIG, DEFAULT_SETTINGS)

    by_surface = {m.surface_form: m for m in result.mentions}
    assert set(by_surface) == {"Wilma.Graham@t.edu", "RMA-3056773", "Wilma", "AAAAAAAAGLMDAAAA"}
    text_of = {s.segment_id: s.text for s in segments}
    for mention in result.mentions:  # every row is a real span
        assert text_of[mention.segment_id][mention.start_offset : mention.end_offset] == mention.surface_form
        assert (mention.extractor, mention.extractor_detail) == ("llm", "llm-1")
    assert by_surface["AAAAAAAAGLMDAAAA"].segment_id != by_surface["Wilma.Graham@t.edu"].segment_id

    names = {e.entity_id: (e.ontology_class, e.external_ids[0]) for e in result.resolution.entities}
    linked = {
        by_id.surface_form: names[link.entity_id]
        for link in result.resolution.links
        for by_id in result.mentions
        if by_id.mention_id == link.mention_id
    }
    assert linked == {  # the bare first name and the unknown item ID link to nothing
        "Wilma.Graham@t.edu": ("Customer", "tpcds.customer:c_customer_sk=2"),
        "RMA-3056773": ("Return", "documents.return:rma=RMA-3056773"),
    }
    assert {l.resolved_by for l in result.resolution.links} == {"exact_key"}
    assert {r.relationship_type for r in result.resolution.relationships} == {"Mentions", "About"}

    [claim] = result.claims
    assert (claim.predicate, claim.extractor) == ("REFUND_REQUESTED", "llm")
    assert names[claim.subject_entity_id][0] == "Customer" and names[claim.object_entity_id][0] == "Return"
    [passage] = result.evidence
    assert passage.excerpt == "When will the refund post?"
    assert text_of[passage.segment_id][passage.locator["start"] : passage.locator["end"]] == passage.excerpt

    assert result.counts["llm_calls"] == 2 and result.counts["llm_tokens_in"] == 200
    assert result.counts["llm_hallucinated_spans"] == 2  # one entity, one claim
    assert result.counts["llm_invalid_items"] == 1  # a class the ontology does not have
    assert result.counts["llm_relocated"] == 1  # right text, wrong segment number
    # A name that did not resolve, the wrong shape, and a subject that was dropped.
    assert result.counts["llm_claims_unanchored"] == 3
    assert "llm_cost_microusd" not in result.counts and "cases" not in result.counts


def test_a_rerun_comes_from_the_cache_and_gives_the_same_rows(gazetteer, tmp_path):  # noqa: F811
    run = _run()
    assets, segments, _ = read_assets(run, gazetteer, {"a-email"})
    first = run_arm(run, assets, segments, extractor(tmp_path), gazetteer, CONFIG, DEFAULT_SETTINGS)
    llm = FakeLlm()
    priced = DEFAULT_SETTINGS.model_copy(
        update={
            "llm": DEFAULT_SETTINGS.llm.model_copy(
                update={"input_price_per_million": 2.0, "output_price_per_million": 6.0}
            )
        }
    )
    again = LlmExtractor(llm, priced, VOCABULARY, tmp_path / "cache")
    second = run_arm(run, assets, segments, again, gazetteer, CONFIG, priced)

    assert llm.calls == []
    assert (second.counts["llm_cached"], second.counts.get("llm_calls", 0)) == (1, 0)
    assert second.counts["llm_cost_microusd"] == 0  # nothing was sent
    assert [m.mention_id for m in second.mentions] == [m.mention_id for m in first.mentions]
    assert [c.claim_id for c in second.claims] == [c.claim_id for c in first.claims]
    assert first.counts["llm_calls"] == 1

    other_model = FakeLlm()
    other_model.model = "fake-2"
    run_arm(run, assets, segments, extractor(tmp_path, other_model), gazetteer, CONFIG, DEFAULT_SETTINGS)
    assert len(other_model.calls) == 1  # another model never reuses a reply


def test_rate_limits_are_waited_out_and_other_failures_are_counted(gazetteer, tmp_path):  # noqa: F811
    run = _run()
    assets, segments, _ = read_assets(run, gazetteer, {"a-email"})
    waits = []
    limited = extractor(tmp_path, FakeLlm(failures=2), waits.append)
    result = run_arm(run, assets, segments, limited, gazetteer, CONFIG, DEFAULT_SETTINGS)
    assert waits == [15, 30] and result.counts["llm_rate_limited"] == 2
    assert len(result.mentions) == 4

    class Broken(FakeLlm):
        def complete_with_usage(self, system, user):
            raise LLMError("fake 500: boom")

    failed = run_arm(
        run, assets, segments, extractor(tmp_path / "other", Broken()), gazetteer, CONFIG, DEFAULT_SETTINGS
    )
    assert failed.mentions == [] and failed.counts["llm_failed"] == 1


def _connector(tmp_path):
    from apps.helios.crawler.connectors import HeliosDsConnector, object_reader

    root = tmp_path / "objects"
    con = duckdb.connect()
    con.execute("CREATE SCHEMA helios_ds")
    con.execute(
        "CREATE TABLE helios_ds.crawlable_artifacts (artifact_id VARCHAR, dataset_id VARCHAR, "
        "artifact_type VARCHAR, mime_type VARCHAR, source_locator VARCHAR, sha256 VARCHAR, "
        "size_bytes BIGINT, semantic_timestamp VARCHAR)"
    )
    kinds = {"message/rfc822": "email", "application/json": "chat", "application/pdf": "pdf"}
    for artifact_id, (mime, data) in ASSETS.items():
        key = f"datasets/ds-1/artifacts/{artifact_id}"
        (root / key).parent.mkdir(parents=True, exist_ok=True)
        (root / key).write_bytes(data)
        locator = {"connector_type": "helios_ds_file", "root": str(root), "key": key}
        con.execute(
            "INSERT INTO helios_ds.crawlable_artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [artifact_id, "ds-1", kinds[mime], mime, json.dumps(locator), hashlib.sha256(data).hexdigest(), len(data), None],
        )
    return HeliosDsConnector("ds-1", {"dataset_id": "ds-1"}, con.cursor, object_reader())


def test_an_llm_crawl_records_its_strategy_and_stays_out_of_search(warehouse, gazetteer, tmp_path):  # noqa: F811
    connector = _connector(tmp_path)
    index = duckdb_index_store()
    index.ensure_tables()
    embedded = []

    def run_crawl(strategy, llm=None):
        return crawl(
            index, connector, "ds-1", actor="test", ontology_version="0.2.0", settings=None,
            settings_hash=DEFAULT_SETTINGS.content_hash(), crawler_settings=DEFAULT_SETTINGS,
            gazetteer=gazetteer, resolution=CONFIG, warehouse_cursor=warehouse.cursor,
            strategy=strategy, llm=llm,
            embed=(lambda run, *_: embedded.append(run.crawl_run_id) or 0) if strategy == "deterministic" else None,
        )

    deterministic = run_crawl("deterministic")
    llm_run = run_crawl("llm", extractor(tmp_path))

    assert deterministic.settings["strategy"] == "deterministic" and "llm" not in deterministic.settings
    assert llm_run.status == "SUCCEEDED" and llm_run.settings["strategy"] == "llm"
    assert llm_run.settings["llm"] == {
        "provider": "fake",
        "model": "fake-1",
        "temperature": 0.0,
        "prompt_version": "llm-1",
        "prompt_hash": llm_arm.system_prompt(DEFAULT_SETTINGS, VOCABULARY) and llm_run.settings["llm"]["prompt_hash"],
    }
    assert "never-recorded" not in json.dumps(llm_run.settings)
    assert llm_run.counts["llm_calls"] == 7 and llm_run.counts["mentions"] == 4
    assert llm_run.counts["claims"] == 1 and "cases" not in llm_run.counts
    mentions = index.read("helios_index.mentions", {"crawl_run_id": llm_run.crawl_run_id})
    assert {m.extractor for m in mentions} == {"llm"}

    # Each strategy reuses only its own previous run, and search keeps the deterministic one.
    assert previous_run(index, "ds-1", DEFAULT_SETTINGS.content_hash()).crawl_run_id == deterministic.crawl_run_id
    assert previous_run(index, "ds-1", DEFAULT_SETTINGS.content_hash(), "llm").crawl_run_id == llm_run.crawl_run_id
    assert [r.crawl_run_id for r in evidence.latest_runs(index)] == [deterministic.crawl_run_id]
    assert embedded == [deterministic.crawl_run_id]

    second = run_crawl("llm", extractor(tmp_path))  # unchanged documents, read again from the cache
    assert second.counts["carried_forward"] == 7
    assert (second.counts["llm_cached"], second.counts.get("llm_calls", 0)) == (7, 0)
    assert second.counts["mentions"] == 4 and second.counts["claims"] == 1

    with pytest.raises(ValueError, match="needs a model"):
        run_crawl("llm")
    with pytest.raises(ValueError, match="unknown crawl strategy"):
        run_crawl("hybrid")
