"""The facades: Catalog, Profiler, Proposer, Publisher (burndown 5.4).

test_round_trip.py runs them end to end and pins the published model; these pin what
each one does with the caller, the run store and the registry, and what it refuses."""
import duckdb
import pytest

from helios_cortex import Caller
from helios_cortex.artifacts import ArtifactStore
from helios_cortex.domain import DataSource, DataSourceReference, Model
from helios_cortex.engines.duckdb_engine import DuckDBEngine
from helios_cortex.registry import InMemoryRegistry
from helios_cortex.runs import RunStore
from helios_cortex.settings import Settings
from helios_model import (Catalog, DiscoveryError, ModelNotRegistered, Profiler, Proposals, Proposer,
                          Publisher, discovery)
from helios_model.publication import PublicationValidationError

RUN = "20261110T120000Z"
SOURCE = DataSource("warehouse", "Warehouse", "duckdb", "default")


class Recording(DuckDBEngine):
    """The settings' DuckDB warehouse, recording whose identity each statement ran as."""

    callers: list = []

    def query(self, sql, caller=None, *, limit=1000):
        Recording.callers.append(caller)
        return super().query(sql, caller, limit=limit)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    warehouse = tmp_path / "warehouse.duckdb"
    with duckdb.connect(str(warehouse)) as db:
        db.execute("CREATE SCHEMA shop")
        db.execute("CREATE TABLE shop.customers (c_customer_id INTEGER, c_name VARCHAR)")
        db.execute("INSERT INTO shop.customers SELECT i, 'c' || i FROM range(1, 21) t(i)")
        db.execute("CREATE TABLE shop.orders (o_order_id INTEGER, o_customer_id INTEGER, o_amount DOUBLE)")
        db.execute("INSERT INTO shop.orders SELECT i, 1 + i % 20, i * 1.5 FROM range(1, 101) t(i)")
    monkeypatch.setattr(discovery, "engine_for", lambda connection: Recording(connection.options["path"]))
    Recording.callers = []
    return Settings(data_dir=tmp_path / "data", connections={"default": {"engine": "duckdb", "path": str(warehouse)}})


class LLM:
    provider, model, calls = "scripted", "m", 0

    def complete_json(self, system, user):
        self.calls += 1
        if "fact_tables" in user:
            return {"metrics": [{"name": "Revenue", "dataset": "shop.orders", "expression": "SUM(o_amount)",
                                 "confidence": 0.8, "rationale": "Money in."}]}
        if '"table": "shop.orders"' in user:
            return {"name": "Orders", "kind": "fact", "confidence": 0.9, "rationale": "Orders with amounts.",
                    "fields": [{"column": "o_amount", "role": "measure", "confidence": 0.9}]}
        if '"table"' in user:
            return {"name": "Customers", "kind": "dimension", "confidence": 0.9, "fields": []}
        return {"decisions": []}


def proposed(settings):
    harvest = Catalog(settings).harvest(SOURCE, None, databases=["shop"], model_id="sales", run_id=RUN)
    profile = Profiler(settings).profile(harvest)
    return Proposer(settings, llm=LLM()).propose(profile)


def registered(settings):
    registry = InMemoryRegistry()
    registry.save_data_source(SOURCE)
    registry.save_model(Model("sales", "Sales", (DataSourceReference("warehouse"),)))
    return registry


def test_harvest_and_profile_run_as_the_caller(settings):
    bob = Caller("bob")
    harvest = Catalog(settings).harvest(SOURCE, bob, databases=["shop"], model_id="sales", run_id=RUN)
    assert harvest.tables == ("shop.customers", "shop.orders")
    assert set(Recording.callers) == {bob}
    stored = RunStore.from_settings(settings).load(RUN, "harvest")
    assert (stored["model_id"], stored["data_source_id"], stored["connection_ref"], stored["harvested_by"]) == (
        "sales", "warehouse", "default", "bob")

    Recording.callers.clear()
    profile = Profiler(settings).profile(RUN, bob)            # by run id, as another process would
    assert set(Recording.callers) == {bob}
    assert [(r["from_column"], r["to"]) for r in profile.relationships] == [("o_customer_id", "shop.customers")]
    assert RunStore.from_settings(settings).load(RUN, "profile")["profiled_by"] == "bob"

    Recording.callers.clear()
    Profiler(settings).profile(harvest)                      # no caller: the service identity
    assert set(Recording.callers) == {None}


def test_harvest_needs_databases_a_connection_and_a_new_run(settings):
    catalog = Catalog(settings)
    with pytest.raises(ValueError, match="at least one database"):
        catalog.harvest(SOURCE, None, databases=[], model_id="sales")
    catalog.harvest(SOURCE, None, databases=["shop"], model_id="sales", run_id=RUN)
    with pytest.raises(DiscoveryError, match="already has a harvest"):
        catalog.harvest(SOURCE, None, databases=["shop"], model_id="sales", run_id=RUN)
    with pytest.raises(DiscoveryError, match="no connection"):
        Catalog(Settings(data_dir=settings.data_dir)).harvest(SOURCE, None, databases=["shop"], model_id="sales")


def test_later_stages_need_the_earlier_ones(settings):
    with pytest.raises(DiscoveryError, match="has no harvest"):
        Profiler(settings).profile(RUN)
    runs = RunStore.from_settings(settings)
    runs.write(RUN, "harvest", {"tables": [], "model_id": "sales"})          # a job's harvest: no connection
    with pytest.raises(DiscoveryError, match="names no connection"):
        Profiler(settings).profile(RUN)
    with pytest.raises(DiscoveryError, match="has no profile"):
        Proposer(settings, llm=LLM()).propose(RUN)
    runs.write(RUN, "profile", {"tables": {}, "relationships": []})
    with pytest.raises(DiscoveryError, match="no LLM configured"):
        Proposer(settings).propose(RUN)


def test_proposals_are_kept_in_the_run_and_as_the_models_latest(settings):
    proposals = proposed(settings)
    assert isinstance(proposals, Proposals)
    assert (proposals.run_id, proposals.model_id) == (RUN, "sales")
    assert [m.rationale for m in proposals.metrics] == ["Money in."]
    runs = RunStore.from_settings(settings)
    assert runs.load(RUN, "propose") == proposals.proposal
    assert ArtifactStore.from_settings(settings).read_json("sales", "proposed", f"{RUN}.proposal.json") == (
        proposals.proposal)

    proposals.accept(("datasets", "shop.orders"))
    proposer = Proposer(settings)
    proposer.save_review(proposals, Caller("carol"))
    loaded = proposer.load(RUN)
    assert loaded.get("datasets", "shop.orders").decision == "accept"
    assert loaded.review["reviewed_by"] == "carol"


def test_publish_registers_the_revision_as_the_caller(settings):
    proposals = proposed(settings)
    proposals.accept_all()
    registry = registered(settings)
    publication = Publisher(settings, registry=registry).publish(proposals, Caller("dana"))
    assert registry.semantic_revisions("sales") == [publication.revision]
    assert (publication.model_id, publication.revision.published_by, publication.revision.discovery_run_id) == (
        "sales", "dana", RUN)
    assert [m["name"] for m in publication.document["metrics"]] == ["revenue"]
    assert (publication.document["name"], publication.document["description"]) == (
        "sales", f"Sales. Generated by helios run {RUN}.")
    assert ArtifactStore.from_settings(settings).active_revision("sales").revision_id == publication.revision_id
    assert RunStore.from_settings(settings).load(RUN, "review") == proposals.review     # what was published

    retry = Publisher(settings, registry=registry).publish(proposals, Caller("erin"))
    assert registry.semantic_revisions("sales") == [retry.revision] == [publication.revision]   # same content

    proposals.reject(proposals.metrics[0])
    service = Publisher(settings, registry=registry).publish(proposals, None)   # the service: no publisher
    assert service.revision.published_by is None
    assert registry.semantic_revisions("sales")[0] == service.revision          # newest first


def test_publish_refuses_before_writing_anything(settings):
    proposals = proposed(settings)
    proposals.accept_all()
    artifacts = ArtifactStore.from_settings(settings)
    with pytest.raises(ModelNotRegistered, match="register it before publishing"):
        Publisher(settings, registry=InMemoryRegistry()).publish(proposals, Caller("dana"))
    assert artifacts.active_revision("sales") is None

    registry = registered(settings)
    proposals.edit(proposals.metrics[0], {"expression": "SUM(c_name)"})        # a column of another table
    with pytest.raises(PublicationValidationError, match="Revenue"):
        Publisher(settings, registry=registry).publish(proposals, Caller("dana"))
    assert (artifacts.active_revision("sales"), registry.semantic_revisions("sales")) == (None, [])
    assert RunStore.from_settings(settings).load(RUN, "review") is None


def test_the_published_description_comes_from_the_registered_model():
    sales = Model("sales", "Sales", (DataSourceReference("warehouse"),), description="Orders and revenue.")
    assert discovery.published_description(sales, RUN) == f"Sales: Orders and revenue. Generated by helios run {RUN}."
    plain = Model("sales", "Sales", (DataSourceReference("warehouse"),), description="  ")
    assert discovery.published_description(plain, RUN) == f"Sales. Generated by helios run {RUN}."
