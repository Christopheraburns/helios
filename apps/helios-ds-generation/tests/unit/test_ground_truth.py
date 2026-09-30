"""C-04: hidden ground truth built from rendered artifacts."""

from collections import Counter

import pytest

from helios_ds.ground_truth import TRUTH_TABLES, ArtifactText, LocatorError, verify_locators
from helios_ds.lifecycle import validate_published
from helios_ds.pipeline import plan_and_publish
from helios_ds.render.base import Mention, RenderedArtifact


def _truth(sink, dataset_id):
    return {t.split(".")[1]: sink.read_dataset(t, dataset_id) for t in TRUTH_TABLES}


@pytest.fixture
def published(make_env, tiny_config, small_repo, templates):
    sink, store = make_env("gt")
    result = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    return sink, store, result


def test_every_stored_locator_resolves_against_the_stored_bytes(published):
    sink, store, result = published
    artifacts = {
        a.artifact_id: a for a in sink.read_dataset("helios_ds.artifacts", result.dataset_id)
    }
    texts = {
        a.artifact_id: ArtifactText(a.mime_type, store.get(a.source_locator["key"]))
        for a in artifacts.values()
    }
    truth = _truth(sink, result.dataset_id)
    assert truth["entity_mentions"] and truth["evidence"]
    for m in truth["entity_mentions"]:
        assert texts[m.artifact_id].check(m.locator, m.surface_form) is True, m
    for e in truth["evidence"]:
        assert texts[e.artifact_id].check(e.locator, e.excerpt) is True, e


def test_each_story_has_evidenced_claims_and_business_relationships(published):
    sink, _, result = published
    truth = _truth(sink, result.dataset_id)
    claims = truth["claims"]
    per_scenario = Counter(c.scenario_id for c in claims)
    assert len(per_scenario) == 3  # tiny config: three return stories
    assert {c.claim_type for c in claims} >= {"PACKAGING_DAMAGED", "REFUND_APPROVED"}
    assert all(c.truth_status == "INTENDED_TRUE" and c.statement for c in claims)

    evidence_types = Counter()
    artifacts = {
        a.artifact_id: a for a in sink.read_dataset("helios_ds.artifacts", result.dataset_id)
    }
    by_claim = {c.claim_id: c for c in claims}
    for e in truth["evidence"]:
        evidence_types[
            (by_claim[e.claim_id].claim_type, artifacts[e.artifact_id].artifact_type)
        ] += 1
    # Packaging damage is stated by every PDF, email and chat.
    for artifact_type in ("pdf", "email", "chat"):
        assert evidence_types[("PACKAGING_DAMAGED", artifact_type)] == 3

    predicates = Counter(r.predicate for r in truth["relationships"])
    for predicate in ("REFERS_TO_SALE", "RETURNS_ITEM", "HAS_REASON", "PURCHASED_IN", "MENTIONS"):
        assert predicates[predicate] > 0, predicate
    assert predicates["DISCUSSES"] == 9  # every artifact discusses its return

    entity_types = Counter(e.entity_type for e in truth["entities"])
    assert entity_types["Artifact"] == 9
    assert {"Customer", "Item", "Store", "Return", "Reason"} <= set(entity_types)


def test_entities_are_shared_across_a_storys_artifacts(published):
    sink, _, result = published
    truth = _truth(sink, result.dataset_id)
    item_mentions = [m for m in truth["entity_mentions"] if m.entity_type == "Item"]
    by_scenario = {}
    for m in item_mentions:
        by_scenario.setdefault(m.scenario_id, set()).add(m.entity_id)
    # One item entity per story, however it is mentioned (name or item id) and wherever.
    assert all(len(ids) == 1 for ids in by_scenario.values())


def test_rerun_writes_nothing_and_a_partial_answer_key_is_rewritten(
    published, tiny_config, small_repo, templates
):
    sink, store, result = published
    before = {
        t: sorted(r.model_dump_json() for r in rows)
        for t, rows in _truth(sink, result.dataset_id).items()
    }
    plan_and_publish(tiny_config, small_repo, templates, sink, store)
    after = {
        t: sorted(r.model_dump_json() for r in rows)
        for t, rows in _truth(sink, result.dataset_id).items()
    }
    assert after == before

    sink.delete_dataset_rows("helios_ground_truth.evidence", result.dataset_id)  # simulate a crash
    from helios_ds.ground_truth import GroundTruthBuilder  # noqa: F401  (documented path)
    from helios_ds.manifests import GenerationManifest, manifest_key
    from helios_ds.render.dataset import render_dataset

    manifest = GenerationManifest.from_bytes(store.get(manifest_key(result.dataset_id)))
    render_dataset(manifest, templates, store, sink)
    repaired = {
        t: sorted(r.model_dump_json() for r in rows)
        for t, rows in _truth(sink, result.dataset_id).items()
    }
    assert repaired == before


def test_validation_reports_orphans(published):
    sink, store, result = published
    assert validate_published(sink, store, result.dataset_id) == []
    sink.delete_dataset_rows("helios_ground_truth.entities", result.dataset_id)
    problems = validate_published(sink, store, result.dataset_id)
    assert any("mentions of unknown entities" in p for p in problems)
    assert any("relationships with unknown endpoints" in p for p in problems)


def test_a_wrong_locator_fails_the_render():
    good = RenderedArtifact(
        data=b'{"messages": [{"message_id": "m1", "text": "The box was crushed"}]}',
        mime_type="application/json",
        extension="json",
        semantic_timestamp="2002-01-01T00:00:00Z",
        mentions=[
            Mention(
                "Item",
                {"table": "item", "i_item_sk": 1},
                "box",
                {"message_id": "m1", "start": 4, "end": 7},
            )
        ],
    )
    assert verify_locators("a1", good).checked == 1
    bad = RenderedArtifact(
        **{
            **good.__dict__,
            "mentions": [Mention("Item", {}, "box", {"message_id": "m1", "start": 0, "end": 3})],
        }
    )
    with pytest.raises(LocatorError, match="does not resolve"):
        verify_locators("a1", bad)


def test_pdf_locators_are_reported_unchecked_without_pypdf(monkeypatch, published):
    import builtins

    sink, store, result = published
    pdf = next(
        a
        for a in sink.read_dataset("helios_ds.artifacts", result.dataset_id)
        if a.artifact_type == "pdf"
    )
    real_import = builtins.__import__

    def no_pypdf(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("no pypdf")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pypdf)
    text = ArtifactText("application/pdf", store.get(pdf.source_locator["key"]))
    assert text.check({"page": 1, "text": "anything"}, "anything") is None


def test_ensure_tables_adds_new_columns_to_existing_tables(make_sink):
    from helios_ds.lakehouse.tables import TABLES, column_names

    sink = make_sink("migrate")
    spec = TABLES["helios_ground_truth.evidence"]
    old_columns = [c for c in column_names(spec) if c not in ("scenario_id", "locator", "excerpt")]
    # Create the table as it was before C-04, then let ensure_tables migrate it.
    if hasattr(sink, "catalog"):
        from pyiceberg.schema import Schema

        from helios_ds.lakehouse.tables import iceberg_schema

        full = iceberg_schema(spec)
        for ns in ("helios_ds", "helios_ground_truth"):
            sink.catalog.create_namespace(ns)
        sink.catalog.create_table(
            spec.identifier, schema=Schema(*[f for f in full.fields if f.name in old_columns])
        )
    else:
        sink._execute("CREATE SCHEMA IF NOT EXISTS helios_ground_truth")
        sink._execute(
            f"CREATE TABLE {spec.full_name} ({', '.join(c + ' VARCHAR' for c in old_columns)})"
        )
    sink.ensure_tables()
    sink.ensure_tables()  # idempotent
    assert sink.read("helios_ground_truth.evidence") == []
    from helios_ds.schemas import TruthEvidenceRecord

    row = TruthEvidenceRecord(
        dataset_id="d",
        evidence_id="e",
        claim_id="c",
        artifact_id="a",
        locator_type="t",
        scenario_id="s",
        locator={"page": 1, "text": "x"},
        excerpt="x",
    )
    sink.append("helios_ground_truth.evidence", [row])
    assert sink.read_dataset("helios_ground_truth.evidence", "d") == [row]
