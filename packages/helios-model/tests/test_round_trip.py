"""Harvest -> profile -> propose -> review -> publish, end to end (burndown 5.2, 5.4).

The data is helios-ds's tiny fixture (apps/helios-ds-generation/fixtures/tiny: twelve
store_returns rows) plus two small dimensions built from its keys, item and reason, so
profiling has foreign keys to find. Everything is local: DuckDB, the SQLite registry
and the artifact directory that Settings default to, and a scripted LLM.

The stages are helios-model's facades (5.4: Catalog, Profiler, Proposer, Publisher),
each built from the Settings alone, as a host builds them. 5.2 wrote this test over the
pieces underneath before the facades existed; the golden file is unchanged since. After
an intended change to the published model:

    HELIOS_UPDATE_GOLDEN=1 uv run pytest packages/helios-model/tests/test_round_trip.py

and review the diff.
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

from helios_cortex import Caller
from helios_cortex.artifacts import ArtifactStore
from helios_cortex.domain import DataSource, DataSourceReference, Model
from helios_cortex.ossie import SemanticModel
from helios_cortex.registry import SqlRegistry
from helios_cortex.runs import RunStore
from helios_cortex.settings import Settings
from helios_model import Catalog, Profiler, Proposer, Publisher
from helios_model.propose import SYSTEM_METRICS, SYSTEM_RELATIONSHIPS, SYSTEM_TABLE

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
TINY = REPO / "apps" / "helios-ds-generation" / "fixtures" / "tiny" / "store_returns.json"
GOLDEN = HERE / "golden" / "round_trip.ossie.yaml"
UPDATE = os.environ.get("HELIOS_UPDATE_GOLDEN") == "1"

RUN_ID = "20261110T120000Z"
REVIEWED_AT = datetime(2026, 11, 10, 13, 0, tzinfo=timezone.utc)
MODEL_ID = "returns"
TABLES = ("tpcds.item", "tpcds.reason", "tpcds.store_returns")

REASONS = {1: "Package was damaged", 3: "Did not like the colour", 5: "Found a better price",
           7: "Product arrived damaged", 9: "Wrong size", 12: "Gift exchange", 2: "Late delivery"}
CATEGORIES = ("Home", "Electronics", "Sports", "Books")


def load_warehouse(path: Path) -> None:
    """The tiny fixture's returns, and the items and reasons its keys refer to (plus a few
    never returned, as in any warehouse)."""
    returns = json.loads(TINY.read_text())
    items = sorted({r["sr_item_sk"] for r in returns} | {9001, 9002})
    with duckdb.connect(str(path)) as db:
        db.execute("CREATE SCHEMA tpcds")
        db.execute("CREATE TABLE tpcds.store_returns (business_key VARCHAR, sr_item_sk INTEGER, "
                   "sr_ticket_number INTEGER, sr_reason_sk INTEGER)")
        db.executemany("INSERT INTO tpcds.store_returns VALUES (?, ?, ?, ?)",
                       [(r["business_key"], r["sr_item_sk"], r["sr_ticket_number"], r["sr_reason_sk"])
                        for r in returns])
        db.execute("CREATE TABLE tpcds.item (i_item_sk INTEGER, i_product_name VARCHAR, i_category VARCHAR)")
        db.executemany("INSERT INTO tpcds.item VALUES (?, ?, ?)",
                       [(sk, f"Product {sk}", CATEGORIES[i % len(CATEGORIES)]) for i, sk in enumerate(items)])
        db.execute("CREATE TABLE tpcds.reason (r_reason_sk INTEGER, r_reason_desc VARCHAR)")
        db.executemany("INSERT INTO tpcds.reason VALUES (?, ?)", sorted(REASONS.items()))


# What a careful LLM answers for each table: kind, names, roles, and glossary terms.
TABLE_ANSWERS = {
    "tpcds.store_returns": {
        "name": "Store returns", "kind": "fact", "confidence": 0.9,
        "description": "One row per item returned to a store, with the ticket it was sold on and the reason.",
        "fields": {
            "business_key": ("Return key", "identifier", "Identifies the returned line: item and ticket.", None),
            "sr_item_sk": ("Item", "foreign_key", "The item that was returned.", None),
            "sr_ticket_number": ("Ticket number", "dimension", "The sales ticket the item was bought on.",
                                 {"name": "Ticket", "definition": "A receipt for one store sale."}),
            "sr_reason_sk": ("Return reason", "foreign_key", "Why the customer returned the item.", None),
        },
    },
    "tpcds.item": {
        "name": "Item", "kind": "dimension", "confidence": 0.95,
        "description": "The products a store sells.",
        "fields": {
            "i_item_sk": ("Item", "identifier", "Identifies the item.", None),
            "i_product_name": ("Product name", "attribute", "The product's display name.", None),
            "i_category": ("Category", "dimension", "The merchandise category.",
                           {"name": "Product category", "definition": "A merchandising group of products."}),
        },
    },
    "tpcds.reason": {
        "name": "Return reason", "kind": "lookup", "confidence": 0.9,
        "description": "Why customers return items.",
        "fields": {
            "r_reason_sk": ("Reason", "identifier", "Identifies the reason.", None),
            "r_reason_desc": ("Reason", "dimension", "The reason in words.",
                              {"name": "Return reason", "definition": "Why a customer returned an item."}),
        },
    },
}


class ScriptedLLM:
    """Stands in for LLMClient: the same attributes, deterministic answers, and a record
    of which prompts were asked."""

    provider, model = "scripted", "round-trip"

    def __init__(self):
        self.calls = 0
        self.asked = []

    def complete_json(self, system: str, user: str) -> dict:
        self.calls += 1
        if system == SYSTEM_TABLE:
            table = json.loads(user.split("\n\nReturn ")[0])["table"]
            self.asked.append(("table", table))
            answer = TABLE_ANSWERS[table]
            return {**{k: answer[k] for k in ("name", "kind", "description", "confidence")},
                    "rationale": f"{answer['kind'].capitalize()} table, from its keys and relationships.",
                    "fields": [{"column": column, "name": name, "role": role, "description": text, "term": term,
                                "confidence": 0.9, "rationale": f"Named {name.lower()}; its statistics fit a {role}."}
                               for column, (name, role, text, term) in answer["fields"].items()]}
        if system == SYSTEM_RELATIONSHIPS:
            self.asked.append(("relationships", None))
            return {"decisions": []}
        if system == SYSTEM_METRICS:
            self.asked.append(("metrics", None))
            return {"metrics": []}
        raise AssertionError(f"unexpected prompt: {system[:60]}")


@pytest.fixture
def settings(tmp_path):
    """Settings for a laptop: a DuckDB warehouse, and the defaults for everything else."""
    warehouse = tmp_path / "warehouse.duckdb"
    load_warehouse(warehouse)
    return Settings(data_dir=tmp_path / "data", connections={"default": {"engine": "duckdb", "path": str(warehouse)}})


def test_a_discovered_model_is_published_to_the_artifacts_and_the_registry(settings):
    alice = Caller("alice")
    registry = SqlRegistry.from_settings(settings)      # the local SQLite default, upgraded on first use

    # The host registers the warehouse and the model before discovery (the console's
    # data source and model forms).
    registry.save_data_source(DataSource("warehouse", "Warehouse", "duckdb", "default"))
    registry.save_model(Model(MODEL_ID, "Store returns", (DataSourceReference("warehouse", TABLES),)),
                        created_by="alice")
    source = registry.data_source("warehouse")

    # harvest, as alice (DuckDB runs everything as its one identity)
    harvest = Catalog(settings).harvest(source, alice, databases=["tpcds"], model_id=MODEL_ID, run_id=RUN_ID)
    assert (harvest.run_id, harvest.model_id, harvest.data_source_id) == (RUN_ID, MODEL_ID, "warehouse")
    assert sorted(harvest.tables) == list(TABLES)
    assert harvest.document["harvested_by"] == "alice"

    # profile: both foreign keys are found from names and data alone
    profile = Profiler(settings).profile(harvest)
    found = sorted((r["from_column"], r["to"], r["to_column"], r["match_ratio"]) for r in profile.relationships)
    assert found == [("sr_item_sk", "tpcds.item", "i_item_sk", 1.0),
                     ("sr_reason_sk", "tpcds.reason", "r_reason_sk", 1.0)]
    assert profile.suggested_relationships == ()

    # propose: one LLM call per table; no suggested relationships to decide, and no
    # measures (the fixture holds keys only), so no metrics to propose
    llm = ScriptedLLM()
    proposals = Proposer(settings, llm=llm).propose(profile)
    assert sorted(llm.asked) == [("table", t) for t in TABLES]
    assert proposals.metrics == ()
    assert [t.label for t in proposals.glossary_terms] == ["Product category", "Return reason", "Ticket"]
    assert {p.decision for p in proposals} == {"pending"}
    # every element has a confidence and a rationale: the LLM's, or the profiler's evidence
    assert all(0 <= p.confidence <= 1 and p.rationale for p in proposals)
    returns = proposals.get("datasets", "tpcds.store_returns")
    assert (returns.kind, returns.rationale) == ("fact", "Fact table, from its keys and relationships.")
    item_key = proposals.get("relationships", "tpcds.store_returns.sr_item_sk->tpcds.item.i_item_sk")
    assert item_key.rationale == ("100% of the 6 distinct values of tpcds.store_returns.sr_item_sk are found "
                                  "in tpcds.item.i_item_sk, and the column name points at that table.")

    # review: the reviewer accepts everything; the decisions are kept in the run
    assert proposals.accept_all(min_confidence=0.0) == 3 + 9 + 2 + 3
    Proposer(settings, llm=llm).save_review(proposals, alice, at=REVIEWED_AT)
    assert Proposer(settings).load(RUN_ID).pending() == ()

    # publish
    publication = Publisher(settings).publish(proposals, alice)
    manifest = publication.manifest

    # The published Ossie YAML is exactly the golden file.
    artifacts = ArtifactStore.from_settings(settings)
    published = artifacts.read_published_ossie(MODEL_ID)
    if UPDATE:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(published)
    assert published == GOLDEN.read_text()

    model = SemanticModel.load_published(MODEL_ID, artifacts)
    assert (model.name, model.doc["description"]) == (            # 5.7: named by the model, not the database
        "returns", f"Store returns. Generated by helios run {RUN_ID}.")
    assert sorted(model.datasets) == ["item", "reason", "store_returns"]
    assert [(r.from_dataset, r.to_dataset) for r in model.join_path("store_returns", "reason")] == [
        ("store_returns", "reason")]

    # The registry holds the data source, the model and one revision that names the
    # published file by path and hash: the one publish returned.
    assert [d.id for d in registry.data_sources()] == ["warehouse"]
    stored = registry.registered_model(MODEL_ID)
    assert stored.created_by == "alice"
    assert stored.model.data_sources == (DataSourceReference("warehouse", TABLES),)
    [revision] = registry.semantic_revisions(MODEL_ID)
    assert revision == publication.revision
    active = artifacts.active_revision(MODEL_ID)
    assert revision.id == active.revision_id == manifest["revision_id"] == publication.revision_id
    assert revision.sha256 == hashlib.sha256(published.encode()).hexdigest()
    assert revision.artifact_path == manifest["path"]
    assert (revision.discovery_run_id, revision.published_by) == (RUN_ID, "alice")
    assert revision.ossie_version == model.doc["version"]
    assert revision.size_bytes == len(published.encode())

    # The run holds every stage's document; the proposal is also the model's latest.
    runs = RunStore.from_settings(settings)
    summary = runs.summary(RUN_ID)
    assert (summary["harvest"]["tables"], summary["profile"]["relationships"], summary["propose"]) == (3, 2, True)
    assert runs.load(RUN_ID, "review")["reviewed_by"] == "alice"
    assert runs.latest_run_id() == RUN_ID
    assert artifacts.read_json(MODEL_ID, "proposed", f"{RUN_ID}.proposal.json") == runs.load(RUN_ID, "propose")
