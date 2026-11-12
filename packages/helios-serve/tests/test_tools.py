"""The seven tools as plain functions (burndown 6.2), on a DuckDB warehouse with a model
published to an artifact store: results, error codes, the caller, the host's hooks,
the audit record, and the diagnosis of a refused credential."""
import json
from pathlib import Path

import duckdb
import pytest

from helios_cortex import Caller, EngineAuthenticationError, IdentityMode, ImpersonationError
from helios_cortex.artifacts import ArtifactStore
from helios_cortex.catalog import ColumnLineage
from helios_cortex.data_authorization import DataPolicy, PlatformDataDecision
from helios_cortex.domain import DataSource, DataSourceReference, Model
from helios_cortex.engines.duckdb_engine import DuckDBEngine
from helios_cortex.ossie import dump_yaml
from helios_cortex.registry import InMemoryRegistry
from helios_cortex.settings import Settings
from helios_serve import ModelStore, Stores, tools

DOCUMENT = json.loads((Path(__file__).parent / "fixtures" / "shop.ossie.json").read_text())
ALICE = Caller("alice")


class Sink:
    def __init__(self):
        self.records = []

    def record(self, event):
        self.records.append(event)


class Platform:
    """The platform allows the query (as Ranger would for this caller)."""

    def can_access(self, caller, resource, action):
        return PlatformDataDecision.allow("allowed by the platform")


class OnlyShop:
    def can_see(self, caller, model_id):
        return model_id == "shop"


@pytest.fixture
def warehouse(tmp_path):
    path = tmp_path / "warehouse.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute("CREATE SCHEMA shop")
        db.execute("CREATE TABLE shop.customers AS SELECT i AS c_customer_id, "
                   "CASE WHEN i % 2 = 0 THEN 'east' ELSE 'west' END AS c_region FROM range(1, 21) t(i)")
        db.execute("CREATE TABLE shop.orders AS SELECT i AS o_order_id, 1 + i % 20 AS o_customer_id, "
                   "i * 1.5 AS o_amount FROM range(1, 101) t(i)")
    return path


def publish(artifacts, model_id="shop", document=DOCUMENT):
    """Publish the fixture as ``model_id`` (its HELIOS extension names the model too)."""
    document = json.loads(json.dumps(document))
    document["name"] = model_id
    for extension in document.get("custom_extensions", []):
        if extension.get("vendor_name") == "HELIOS":
            extension["data"] = json.dumps({**json.loads(extension["data"]), "model_id": model_id})
    return artifacts.write_published_ossie(model_id, document, dump_yaml(document), {"run_id": "r1"})


@pytest.fixture
def stores(tmp_path, warehouse):
    artifacts = ArtifactStore(tmp_path / "artifacts")
    publish(artifacts)
    registry = InMemoryRegistry()
    registry.save_data_source(DataSource("warehouse", "Warehouse", "duckdb", "default"))
    registry.save_model(Model("shop", "Shop", (DataSourceReference("warehouse"),)))
    settings = Settings(data_dir=tmp_path / "data", connections={"default": {"engine": "duckdb", "path": str(warehouse)}})
    return Stores(settings, registry, ModelStore(artifacts), data_policy=DataPolicy(Platform()), audit=Sink())


def test_the_model_is_listed_described_and_searched(stores):
    assert tools.list_models(ALICE, stores) == {"models": [{"name": "shop", "status": "published"}]}
    described = tools.describe_model(ALICE, stores)                        # the first model by default
    assert (described["model"], described["status"]) == ("shop", "published")
    assert {d["table"] for d in described["datasets"]} == {"shop.orders", "shop.customers"}

    matches = tools.search_semantics(ALICE, stores, question="revenue by region")["matches"]
    assert [(m["kind"], m["name"]) for m in matches][:2] == [("metric", "revenue"), ("field", "Region")]
    region = next(m for m in matches if m["kind"] == "field")
    assert region["column"] == "shop.customers.c_region"

    assert tools.describe(ALICE, stores, name="shop.orders")["kind"] == "dataset"
    assert tools.describe(ALICE, stores, name="revenue")["expression"] == "SUM(orders.o_amount)"
    assert tools.describe(ALICE, stores, name="shop.customers.c_region")["kind"] == "field"
    assert tools.describe(ALICE, stores, name="weather")["error"] == "semantic_not_found"


def test_compile_uses_the_models_own_dialect_or_the_one_named(stores):
    compiled = tools.compile_query(ALICE, stores, metrics=["revenue"], dimensions=["shop.customers.c_region"])
    assert (compiled["dialect"], compiled["dataset"], compiled["joins"]) == ("duckdb", "shop.orders", ["shop.customers"])
    assert tools.compile_query(ALICE, stores, metrics=["revenue"], dialect="trino")["dialect"] == "trino"
    assert tools.compile_query(ALICE, stores, metrics=["revenue"], dialect="cobol")["error"] == "invalid_semantic_query"
    wrong = tools.compile_query(ALICE, stores, metrics=["profit"])
    assert wrong["error"] == "invalid_semantic_query" and "search_semantics" in wrong["message"]

    publish(stores.models.artifacts, "draft")                                 # published, but not registered
    unregistered = tools.compile_query(ALICE, stores, metrics=["revenue"], model="draft")
    assert unregistered["error"] == "dialect_required"
    assert tools.compile_query(ALICE, stores, metrics=["revenue"], model="draft", dialect="duckdb")["sql"]


def test_run_query_runs_on_the_models_connection(stores):
    result = tools.run_query(ALICE, stores, metrics=["revenue", "order_count"], dimensions=["shop.customers.c_region"],
                             limit=5000)
    assert result["columns"] == ["c_region", "revenue", "order_count"]
    assert sorted(result["rows"]) == [["east", "3750.0", 50], ["west", "3825.0", 50]]   # decimals as text, as before
    assert result["data_source_ids"] == ["warehouse"]

    [*_, record] = stores.audit.records
    assert (record.action, record.outcome, record.caller, record.row_count) == ("run_query", "success", "alice", 2)
    assert record.sql_fingerprint and result["sql"] not in str(record.as_dict())


def test_the_default_policy_runs_only_queries_the_warehouse_runs_as_the_caller(stores):
    stores.data_policy = DataPolicy()                 # DuckDB has one identity, so the caller is not enforced
    denied = tools.run_query(ALICE, stores, metrics=["revenue"])
    assert denied["error"] == "data_authorization_denied"
    assert stores.audit.records[-1].outcome == "denied"


def test_hidden_and_missing_models_look_the_same(stores):
    publish(stores.models.artifacts, "finance")
    stores.visibility = OnlyShop()
    assert tools.list_models(ALICE, stores)["models"] == [{"name": "shop", "status": "published"}]
    hidden = tools.describe_model(ALICE, stores, model="finance")
    missing = tools.describe_model(ALICE, stores, model="nothing")
    assert hidden["error"] == missing["error"] == "model_not_found"
    assert tools.run_query(ALICE, stores, metrics=["revenue"], model="finance")["error"] == "model_not_found"


def test_connection_errors(stores):
    stores.engines = lambda connection: None
    assert tools.run_query(ALICE, stores, metrics=["revenue"])["error"] == "connection_unavailable"
    stores.registry.save_data_source(DataSource("lake", "Lake", "trino", "lake"))
    stores.settings = Settings(data_dir=stores.settings.data_dir, connections={
        "default": {"engine": "duckdb"}, "lake": {"engine": "duckdb"}})
    stores.registry.save_model(Model("shop", "Shop", (DataSourceReference("warehouse"), DataSourceReference("lake"))))
    conflict = tools.run_query(ALICE, stores, metrics=["revenue"])
    assert conflict["error"] == "connection_conflict" and "default, lake" in conflict["message"]


class Refusing(DuckDBEngine):
    """A warehouse whose query fails as ``failure`` says, and whose ping answers ``ping``."""

    def __init__(self, failure, ping=True, identity=IdentityMode.SERVICE):
        super().__init__()
        self.failure, self._ping = failure, ping
        self.identity_mode = identity

    def query(self, sql, caller=None, *, limit=1000):
        raise self.failure

    def ping(self):
        if isinstance(self._ping, Exception):
            raise self._ping
        return self._ping

    def rejected_value_type(self, error):
        return "not comparable" in str(error)


@pytest.mark.parametrize(("engine", "code", "retryable"), [
    (Refusing(EngineAuthenticationError("401"), identity=IdentityMode.TOKEN), "caller_token_rejected", False),
    (Refusing(EngineAuthenticationError("401"), ping=True), "proxy_delegation_denied", False),
    (Refusing(EngineAuthenticationError("401"), ping=EngineAuthenticationError("401")),
     "workload_authentication_failed", False),
    (Refusing(EngineAuthenticationError("401"), ping=OSError("down")), "warehouse_authentication_failed", True),
    (Refusing(EngineAuthenticationError("401"), ping=False), "warehouse_authentication_failed", True),
    (Refusing(ImpersonationError("ran as svc-helios")), "proxy_delegation_denied", False),
    (Refusing(PermissionError("no")), "query_denied", False),
    (Refusing(RuntimeError("BIGINT and STRING are not comparable")), "invalid_filter_value_type", False),
    (Refusing(RuntimeError("socket closed")), "query_unavailable", True),
])
def test_a_failed_query_is_diagnosed(stores, engine, code, retryable):
    stores.engines = lambda connection: engine
    result = tools.run_query(ALICE, stores, metrics=["revenue"])
    assert (result["error"], result["retryable"]) == (code, retryable)
    assert tools.DIAGNOSTICS not in result                       # diagnostics go to the audit record only
    record = stores.audit.records[-1]
    assert record.error_code == code
    if code not in ("query_denied",):
        assert record.details["diagnostics"]["exception_chain"]


def test_the_sql_is_kept_out_of_the_diagnostics(stores):
    sql = tools.compile_query(ALICE, stores, metrics=["revenue"])["sql"]
    stores.engines = lambda connection: Refusing(RuntimeError(f"failed running: {sql}"))
    tools.run_query(ALICE, stores, metrics=["revenue"])
    diagnostics = stores.audit.records[-1].details["diagnostics"]
    assert sql not in json.dumps(diagnostics) and "[redacted]" in json.dumps(diagnostics)


class Lineage:
    def column_lineage(self, database, table, column, depth=3):
        if column != "o_amount":
            return None
        return ColumnLineage(f"{database}.{table}.{column}", {"a": {"type": "column", "name": "o_amount"}}, [])


def test_lineage_comes_from_a_catalog_that_has_it(stores):
    assert tools.explain_lineage(ALICE, stores, column="shop.orders.o_amount")["error"] == "lineage_unavailable"
    stores.catalogs = lambda: Lineage()
    assert tools.explain_lineage(ALICE, stores, column="shop.orders.o_amount")["entities"]
    assert tools.explain_lineage(ALICE, stores, column="o_amount")["error"] == "invalid_semantic_reference"
    assert tools.explain_lineage(ALICE, stores, column="shop.orders.nope")["error"] == "semantic_not_found"


def test_a_pinned_revision_and_the_active_one(stores):
    artifacts = stores.models.artifacts
    first = artifacts.active_revision("shop").revision_id
    changed = {**DOCUMENT, "description": "A newer revision"}
    publish(artifacts, "shop", changed)
    source, model = stores.models.load("shop")
    assert (source["status"], model.doc["description"]) == ("published", "A newer revision")
    source, model = stores.models.load("shop", revision=first)
    assert (source["status"], source["revision_id"]) == ("published revision", first)
    assert model.doc["description"] == DOCUMENT["description"]
    assert stores.models.load("shop", revision=first)[1] is model           # read and parsed once


def test_a_raising_tool_is_recorded_and_raises(stores):
    class Broken:
        def sources(self):
            raise RuntimeError("store down")

    stores.models = Broken()
    with pytest.raises(RuntimeError):
        tools.list_models(ALICE, stores)
    assert (stores.audit.records[-1].outcome, stores.audit.records[-1].exception) == ("error", "RuntimeError")


def test_from_settings_reads_the_registry_and_artifacts(tmp_path, warehouse):
    settings = Settings(data_dir=tmp_path / "data", connections={"default": {"engine": "duckdb", "path": str(warehouse)}})
    publish(ArtifactStore.from_settings(settings))
    stores = Stores.from_settings(settings)
    assert tools.list_models(None, stores)["models"] == [{"name": "shop", "status": "published"}]
    assert stores.audit is None and tools.describe_model(None, stores)["model"] == "shop"


def test_query_values_keeps_the_engines_values_and_run_query_makes_them_json(stores):
    """query_values (6.6) is run_query for a Python program: same audit, native values, not a registered tool."""
    from decimal import Decimal

    from helios_serve import load_registry

    native = tools.query_values(ALICE, stores, metrics=["revenue"])
    assert isinstance(native["rows"][0][0], Decimal)
    assert stores.audit.records[-1].action == "run_query" and stores.audit.records[-1].row_count == 1
    as_json = tools.run_query(ALICE, stores, metrics=["revenue"])
    assert as_json["rows"] == [[str(native["rows"][0][0])]] and as_json["sql"] == native["sql"]
    assert tools.query_values(ALICE, stores, metrics=["profit"])["error"] == "invalid_semantic_query"
    assert "query_values" not in load_registry()
