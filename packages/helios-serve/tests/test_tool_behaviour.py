"""Tool behaviour that the old MCP server's tests (apps/helios/tests/test_talk_to_data.py,
test_data_authorization.py) pinned, now pinned against helios-serve's functions (burndown
6.7): filter values typed by the semantic model, the messages an agent recovers from, the
row cap and the caller reaching the engine, the default policy failing closed in every
identity mode, secrets kept out of diagnostics, and which connection a model's data
sources resolve to. The model is the old tests' customer360 (fixtures/customer360.ossie.json,
built once from their draft); the repository's TPC-DS model is compiled too."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from helios_cortex import Caller, EngineAuthenticationError, IdentityMode
from helios_cortex.artifacts import ArtifactStore
from helios_cortex.data_authorization import DataPolicy
from helios_cortex.domain import DataSource, DataSourceReference, Model
from helios_cortex.engines import QueryResult
from helios_cortex.engines.duckdb_engine import DuckDBEngine
from helios_cortex.ossie import SemanticModel
from helios_cortex.registry import InMemoryRegistry
from helios_cortex.settings import Settings
from helios_serve import ModelStore, Stores, model_connection, tools

from test_tools import Sink, publish

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CUSTOMER360 = json.loads((HERE / "fixtures" / "customer360.ossie.json").read_text())
OWNER = Caller("owner")


class Recording(DuckDBEngine):
    """A warehouse in ``identity`` mode that records each query and answers one row."""

    def __init__(self, identity=IdentityMode.IMPERSONATE, rows=((12,),)):
        super().__init__()
        self.identity_mode = identity
        self.rows = rows
        self.queries = []

    def query(self, sql, caller=None, *, limit=1000):
        self.queries.append({"sql": sql, "caller": caller, "limit": limit})
        return QueryResult(["customer_count"], [tuple(r) for r in self.rows])


@pytest.fixture
def engine():
    return Recording()


@pytest.fixture
def stores(tmp_path, engine):
    artifacts = ArtifactStore(tmp_path / "artifacts")
    publish(artifacts, "customer360", CUSTOMER360)
    registry = InMemoryRegistry()
    registry.save_data_source(DataSource("warehouse", "Warehouse", "impala", "default"))
    registry.save_model(Model("customer360", "Customer 360", (DataSourceReference("warehouse"),)))
    settings = Settings(data_dir=tmp_path / "data", connections={"default": {"engine": "duckdb"}})
    return Stores(settings, registry, ModelStore(artifacts), audit=Sink(), engines=lambda connection: engine)


# ---------------------------------------------------------------------------- filter values
def test_a_quoted_number_becomes_a_number_for_an_integer_field(stores):
    compiled = tools.compile_query(OWNER, stores, metrics=["Customer count"], dialect="hive",
                                   filters=[{"column": "crm.customers.customer_id", "op": "=", "value": "2002"}])
    assert "customers.customer_id = 2002" in compiled["sql"] and "'2002'" not in compiled["sql"]


def test_a_value_of_the_wrong_type_is_refused_before_the_warehouse(stores, engine):
    for run in (tools.compile_query, tools.run_query):
        refused = run(OWNER, stores, metrics=["Customer count"],
                      filters=[{"column": "crm.customers.customer_id", "op": "=", "value": "2001-10-01"}])
        assert refused["error"] == "invalid_filter_value_type" and refused["retryable"] is False
        assert "expects INTEGER" in refused["message"] and "without quotes" in refused["message"]
    as_text = tools.compile_query(OWNER, stores, metrics=["customer_count"],
                                  filters=[{"column": "crm.customers.region", "op": "=", "value": 12}])
    assert as_text["error"] == "invalid_filter_value_type" and "expects STRING" in as_text["message"]
    assert engine.queries == []


# ---------------------------------------------------------------------------- what an agent recovers from
def test_unknown_names_say_how_to_recover(stores):
    metric = tools.run_query(OWNER, stores, metrics=["total_store_sales"], dimensions=["item.item_category"])
    field = tools.run_query(OWNER, stores, metrics=["Customer count"], dimensions=["item.item_category"])
    assert metric["error"] == field["error"] == "invalid_semantic_query"
    assert "unknown metric" in metric["message"] and "search_semantics" in metric["message"]
    assert "unknown field item.item_category" in field["message"]
    assert tools.describe(OWNER, stores, name="not-a-semantic-object") == {
        "error": "semantic_not_found",
        "message": "nothing named 'not-a-semantic-object' is available in the authorized model",
        "retryable": False,
    }


def test_the_published_model_is_described_searched_and_compiled(stores):
    assert tools.describe_model(OWNER, stores, model="customer360")["datasets"][0]["table"] == "crm.customers"
    fields = [m for m in tools.search_semantics(OWNER, stores, question="customer region")["matches"]
              if m["kind"] == "field"]
    assert {"column": "crm.customers.region", "datatype": "String"}.items() <= fields[0].items()
    assert tools.describe(OWNER, stores, name="crm.customers.customer_id")["column"] == "customer_id"
    compiled = tools.compile_query(OWNER, stores, metrics=["customer_count"], dimensions=["crm.customers.region"])
    assert compiled["dataset"] == "crm.customers" and "GROUP BY" in compiled["sql"]


# ---------------------------------------------------------------------------- running as the caller
def test_the_query_runs_as_the_caller_with_the_row_cap(stores, engine):
    stores.data_policy = DataPolicy()                        # the default: the warehouse enforces the caller
    result = tools.run_query(OWNER, stores, metrics=["Customer count"], limit=5000)
    assert result["rows"] == [[12]]
    [query] = engine.queries
    assert (query["caller"], query["limit"]) == (OWNER, tools.MAX_ROWS)
    assert query["sql"] == result["sql"]
    assert [(r.action, r.outcome, r.caller) for r in stores.audit.records] == [("run_query", "success", "owner")]


@pytest.mark.parametrize(("identity", "caller", "runs"), [
    (IdentityMode.IMPERSONATE, Caller("owner"), True),
    (IdentityMode.IMPERSONATE, Caller(token="jwt"), False),     # no username to run as
    (IdentityMode.IMPERSONATE, None, False),                    # no caller at all
    (IdentityMode.TOKEN, Caller("owner", "jwt"), True),
    (IdentityMode.TOKEN, Caller("owner"), False),               # no token to run with
    (IdentityMode.SERVICE, Caller("owner"), False),             # the warehouse cannot enforce owner
    (IdentityMode.SERVICE, None, False),
])
def test_the_default_policy_fails_closed_without_the_callers_identity(stores, identity, caller, runs):
    engine = Recording(identity)
    stores.engines = lambda connection: engine
    stores.data_policy = DataPolicy()
    result = tools.run_query(caller, stores, metrics=["Customer count"])
    assert ("rows" in result) is runs
    assert bool(engine.queries) is runs
    if not runs:
        assert result["error"] == "data_authorization_denied"
        assert stores.audit.records[-1].outcome == "denied"


def test_secrets_in_the_environment_stay_out_of_the_diagnostics(stores, monkeypatch):
    monkeypatch.setenv("WORKLOAD_PASSWORD", "private-password")

    class Failing(Recording):
        def query(self, sql, caller=None, *, limit=1000):
            raise RuntimeError(f"connection failed with private-password while running {sql}")

    stores.engines = lambda connection: Failing()
    result = tools.run_query(OWNER, stores, metrics=["Customer count"])
    assert result["error"] == "query_unavailable" and tools.DIAGNOSTICS not in result
    diagnostics = stores.audit.records[-1].details["diagnostics"]
    rendered = json.dumps(diagnostics)
    assert diagnostics["stage"] == "query" and "RuntimeError" in rendered
    assert "private-password" not in rendered and "SELECT" not in rendered


def test_a_refused_impersonation_is_told_apart_from_refused_service_credentials(stores):
    class Delegation(Recording):
        def __init__(self, service_works):
            super().__init__(IdentityMode.IMPERSONATE)
            self.service_works = service_works

        def query(self, sql, caller=None, *, limit=1000):
            raise EngineAuthenticationError("HTTP 401")

        def ping(self):
            if not self.service_works:
                raise EngineAuthenticationError("HTTP 401")
            return True

    stores.engines = lambda connection: Delegation(service_works=True)
    assert tools.run_query(OWNER, stores, metrics=["Customer count"])["error"] == "proxy_delegation_denied"
    stores.engines = lambda connection: Delegation(service_works=False)
    refused = tools.run_query(OWNER, stores, metrics=["Customer count"])
    assert refused["error"] == "workload_authentication_failed" and not refused["retryable"]
    assert "service" in refused["message"] and "credentials" in refused["message"]


# ---------------------------------------------------------------------------- which connection
def test_a_models_data_sources_resolve_to_one_connection():
    sources = {"warehouse": SimpleNamespace(connection_ref="impala"),      # recorded before named connections
               "sales": SimpleNamespace(connection_ref="sales"),
               "other-warehouse": SimpleNamespace(connection_ref="env:IMPALA_HOST")}
    registry = SimpleNamespace(data_source=sources.get)
    settings = Settings(connections={"default": {"engine": "duckdb"}, "sales": {"engine": "duckdb"}})

    def model(*ids):
        return SimpleNamespace(data_sources=[SimpleNamespace(data_source_id=i) for i in ids])

    assert model_connection(settings, registry, model("warehouse", "other-warehouse")) == ("default", None)
    assert model_connection(settings, registry, model("sales")) == ("sales", None)
    assert model_connection(settings, registry, model()) == ("default", None)
    assert model_connection(settings, registry, model("unknown")) == ("default", None)
    name, conflict = model_connection(settings, registry, model("warehouse", "sales"))
    assert name is None and "default, sales" in conflict
    assert model_connection(Settings(), registry, model("warehouse")) == (None, None)


# ---------------------------------------------------------------------------- the TPC-DS model
def test_the_repositorys_tpcds_model_compiles(tmp_path):
    document = SemanticModel.load(str(REPO / "models" / "published" / "tpcds.ossie.yaml")).doc
    artifacts = ArtifactStore(tmp_path / "artifacts")
    publish(artifacts, "tpcds", document)
    stores = Stores(Settings(data_dir=tmp_path / "data"), InMemoryRegistry(), ModelStore(artifacts))
    matches = tools.search_semantics(OWNER, stores, question="store sales revenue", limit=5)["matches"]
    assert any(m["kind"] == "metric" for m in matches)
    compiled = tools.compile_query(OWNER, stores, metrics=["Store Sales Revenue"], dimensions=["tpcds.date_dim.d_year"],
                                   filters=[{"column": "tpcds.date_dim.d_year", "op": "=", "value": 2001}],
                                   limit=20, dialect="hive")
    assert (compiled["dataset"], compiled["joins"]) == ("tpcds.store_sales", ["tpcds.date_dim"])
    assert "store_sales_revenue" in compiled["columns"] and "WHERE" in compiled["sql"]
