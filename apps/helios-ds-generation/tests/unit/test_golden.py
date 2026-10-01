"""C-08: golden questions generated with the dataset."""

import re
from collections import Counter

import pytest

from helios_ds.config import ArtifactConfig, DatasetConfig, ScenarioConfig
from helios_ds.golden import GOLDEN_TABLES, KINDS
from helios_ds.ground_truth import ArtifactText
from helios_ds.lifecycle import validate_published
from helios_ds.pipeline import plan_and_publish
from helios_ds.schemas import ExpectedQueryRecord

CONFIG = DatasetConfig(
    master_seed=7,
    artifacts={t: ArtifactConfig(target_count=30) for t in ("pdf", "email", "chat")},
    scenarios={"product_return_damage": ScenarioConfig(weight=1.0)},
)


@pytest.fixture(scope="module")
def golden(tmp_path_factory, small_tpcds_path):
    import duckdb

    from helios_ds.lakehouse import DUCKDB, SqlLakehouseSink
    from helios_ds.object_store import LocalObjectStore
    from helios_ds.templates import TemplateRegistry
    from helios_ds.tpcds import DuckDbTpcds

    root = tmp_path_factory.mktemp("golden")
    sink = SqlLakehouseSink(lambda: duckdb.connect(str(root / "l.duckdb")), DUCKDB)
    sink.ensure_tables()
    store = LocalObjectStore(str(root / "objects"))
    repo = DuckDbTpcds.open(small_tpcds_path)
    result = plan_and_publish(CONFIG, repo, TemplateRegistry.load(), sink, store)
    d = result.dataset_id
    queries = {q.query_id: q for q in sink.read_dataset(GOLDEN_TABLES[0], d)}
    results = {r.query_id: r for r in sink.read_dataset(GOLDEN_TABLES[1], d)}
    return sink, store, repo, result, queries, results


def test_all_six_kinds_are_generated_and_each_question_has_one_answer(golden):
    _, _, _, result, queries, results = golden
    kinds = Counter(q.kind for q in queries.values())
    assert set(kinds) == set(KINDS), kinds
    assert all(1 <= n <= 10 for n in kinds.values())
    assert set(results) == set(queries)
    assert result.golden == dict(sorted(kinds.items()))
    assert all(q.principal_id == "evaluator" for q in queries.values())


def test_structured_answers_match_tpcds(golden):
    _, _, repo, _, queries, _ = golden
    checked = 0
    for q in queries.values():
        if q.required_structured:
            rows = repo.query(q.required_structured["sql"])
            got = [[str(v) for v in r.values()] for r in rows]
            want = [[str(v) for v in r] for r in q.required_structured["rows"]]
            assert got == want, q.question
            checked += 1
    assert checked >= 3


def test_evidence_resolves_and_matches_the_claims(golden):
    sink, store, _, result, queries, results = golden
    d = result.dataset_id
    evidence = {e.evidence_id: e for e in sink.read_dataset("helios_ground_truth.evidence", d)}
    artifacts = {a.artifact_id: a for a in sink.read_dataset("helios_ds.artifacts", d)}
    for q in queries.values():
        for evidence_id in q.required_evidence:
            e = evidence[evidence_id]
            assert e.claim_id in q.required_claims
            assert e.artifact_id in q.required_artifacts
            a = artifacts[e.artifact_id]
            text = ArtifactText(a.mime_type, store.get(a.source_locator["key"]))
            assert text.check(e.locator, e.excerpt) is not False
        if q.kind in ("unstructured", "resolution", "joined", "cross_document"):
            assert q.required_claims and q.required_evidence, q.question
            assert results[q.query_id].result_data["evidence"]


def test_resolution_questions_use_an_alias_unique_to_one_entity(golden):
    sink, _, _, result, queries, results = golden
    mentions = sink.read_dataset("helios_ground_truth.entity_mentions", result.dataset_id)
    resolution = [q for q in queries.values() if q.kind == "resolution"]
    assert resolution
    for q in resolution:
        data = results[q.query_id].result_data
        assert q.difficulty == "alias" and data["alias"] in q.question
        assert {m.entity_id for m in mentions if m.surface_form == data["alias"]} == {
            data["resolves_to"]
        }


def test_no_answer_questions_name_real_items_no_document_mentions(golden):
    sink, store, repo, result, queries, results = golden
    texts = []
    for a in sink.read_dataset("helios_ds.artifacts", result.dataset_id):
        texts.append(store.get(a.source_locator["key"]).decode("latin-1"))
    no_answer = [q for q in queries.values() if q.kind == "no_answer"]
    assert no_answer
    for q in no_answer:
        assert results[q.query_id].result_data["abstain"] is True
        item_id = q.required_structured["rows"][0][0]
        assert repo.query(q.required_structured["sql"])
        assert not any(item_id in t for t in texts)
        assert not q.required_claims and not q.required_evidence


def test_questions_are_filled_in(golden):
    _, _, _, _, queries, _ = golden
    for q in queries.values():
        assert not re.search(r"\{\w+\}", q.question), q.question


def test_validation_rejects_a_question_citing_unknown_evidence(golden):
    sink, store, _, result, queries, _ = golden
    bad = next(iter(queries.values())).model_copy(
        update={"query_id": "bad", "required_evidence": ["no-such-evidence"]}
    )
    sink.append(GOLDEN_TABLES[0], [ExpectedQueryRecord.model_validate(bad.model_dump())])
    problems = validate_published(sink, store, result.dataset_id)
    assert any("unknown evidence" in p for p in problems)
    assert any("without exactly one answer" in p for p in problems)


def test_rerunning_a_published_dataset_never_adds_or_changes_questions(
    make_env, tiny_config, small_repo, templates
):
    from helios_ds.object_store import DeterminismIntegrityError

    sink, store = make_env("g")
    first = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    d = first.dataset_id
    before = sink.read_dataset(GOLDEN_TABLES[0], d)
    assert before
    again = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    assert again.dataset_id == d
    assert sink.read_dataset(GOLDEN_TABLES[0], d) == before

    # Different stored questions are an integrity error, never silently replaced.
    changed = before[0].model_copy(update={"question": "changed?"})
    sink.delete_dataset_rows(GOLDEN_TABLES[0], d)
    sink.append(GOLDEN_TABLES[0], [changed] + before[1:])
    with pytest.raises(DeterminismIntegrityError, match="golden questions"):
        plan_and_publish(tiny_config, small_repo, templates, sink, store)

    # A dataset published before C-08 (no questions) gets none added by a re-run.
    for table in GOLDEN_TABLES:
        sink.delete_dataset_rows(table, d)
    plan_and_publish(tiny_config, small_repo, templates, sink, store)
    assert sink.read_dataset(GOLDEN_TABLES[0], d) == []
