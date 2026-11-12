"""The Helios facade (burndown 6.6) on a DuckDB laptop: discover a model with
helios-model (a scripted LLM), publish it through the facade, then list, search, compile
and query it; who the queries run as; and the errors a developer sees."""
from decimal import Decimal

import duckdb
import pytest

from helios_cortex import Caller, IdentityMode
from helios_cortex.data_authorization import DataPolicy, PlatformDataDecision
from helios_cortex.domain import DataSource, DataSourceReference, Model
from helios_cortex.registry import SqlRegistry
from helios_cortex.settings import Settings
from helios_model import Catalog, Profiler, Proposer
from helios_semantic import CompiledQuery, Helios, HeliosError, QueryResult

RUN = "20261112T120000Z"
SOURCE = DataSource("warehouse", "Warehouse", "duckdb", "default")


class LLM:
    """What a careful LLM proposes for the two tables."""

    provider, model, calls = "scripted", "m", 0

    def complete_json(self, system, user):
        if "fact_tables" in user:
            return {"metrics": [{"name": "Revenue", "dataset": "shop.orders", "expression": "SUM(o_amount)",
                                 "confidence": 0.8, "rationale": "Money in."}]}
        if '"table": "shop.orders"' in user:
            return {"name": "Orders", "kind": "fact", "confidence": 0.9,
                    "fields": [{"column": "o_amount", "role": "measure", "confidence": 0.9}]}
        if '"table"' in user:
            return {"name": "Customers", "kind": "dimension", "confidence": 0.9,
                    "fields": [{"column": "c_region", "role": "dimension", "label": "Region", "confidence": 0.9}]}
        return {"decisions": []}


@pytest.fixture
def settings(tmp_path):
    warehouse = tmp_path / "warehouse.duckdb"
    with duckdb.connect(str(warehouse)) as db:
        db.execute("CREATE SCHEMA shop")
        db.execute("CREATE TABLE shop.customers AS SELECT i AS c_customer_id, "
                   "CASE WHEN i % 2 = 0 THEN 'east' ELSE 'west' END AS c_region FROM range(1, 21) t(i)")
        db.execute("CREATE TABLE shop.orders AS SELECT i AS o_order_id, 1 + i % 20 AS o_customer_id, "
                   "i * 1.5 AS o_amount FROM range(1, 101) t(i)")
    settings = Settings(data_dir=tmp_path / "data", connections={"default": {"engine": "duckdb",
                                                                             "path": str(warehouse)}})
    registry = SqlRegistry.from_settings(settings)
    registry.save_data_source(SOURCE)
    registry.save_model(Model("sales", "Sales", (DataSourceReference("warehouse"),)))
    harvest = Catalog(settings).harvest(SOURCE, None, databases=["shop"], model_id="sales", run_id=RUN)
    Proposer(settings, llm=LLM()).propose(Profiler(settings).profile(harvest))
    return settings


@pytest.fixture
def helios(settings):
    helios = Helios.from_settings(settings)
    helios.publish(RUN, accept_pending=True)
    return helios


def test_publish_then_list_search_compile_and_query(settings):
    helios = Helios.from_settings(settings)
    assert helios.models() == []
    publication = helios.publish(RUN, accept_pending=True)
    assert publication.model_id == "sales" and publication.revision.published_by is None   # this program's user

    assert helios.models() == [{"name": "sales", "status": "published"}]
    assert helios.describe()["model"] == "sales"
    assert helios.search("revenue")[0]["name"] == "revenue"           # published names are slugs

    compiled = helios.compile("Revenue", "shop.customers.c_region")              # a name or a list
    assert isinstance(compiled, CompiledQuery) and compiled.dialect == "duckdb"
    assert compiled.dataset == "shop.orders" and compiled.joins == ("shop.customers",)
    assert "SUM(orders.o_amount)" in compiled.sql
    assert helios.compile(["Revenue"], dialect="trino").dialect == "trino"

    result = helios.query(["Revenue"], ["shop.customers.c_region"],
                          filters=[{"column": "shop.customers.c_region", "op": "=", "value": "east"}])
    assert isinstance(result, QueryResult) and result.sql.startswith("SELECT")
    assert result.records() == [{"c_region": "east", "revenue": Decimal("3750.0")}]   # the engine's values


def test_a_tool_error_is_a_helios_error(helios):
    with pytest.raises(HeliosError) as raised:
        helios.query(["Profit"])
    assert raised.value.code == "invalid_semantic_query" and "search_semantics" in raised.value.message
    assert not raised.value.retryable
    with pytest.raises(HeliosError, match="model_not_found"):
        helios.describe("nowhere")


def test_publishing_is_refused_before_anything_is_written(settings, tmp_path):
    helios = Helios.from_settings(settings)
    with pytest.raises(HeliosError, match="discovery_failed"):
        helios.publish("19990101T000000Z")
    unregistered = Helios.from_settings(settings, registry=SqlRegistry(f"sqlite:///{tmp_path / 'other.db'}"))
    unregistered.stores.registry.upgrade()
    with pytest.raises(HeliosError, match="model_not_registered"):
        unregistered.publish(RUN, accept_pending=True)
    assert helios.models() == []


def test_a_caller_runs_queries_as_themselves(helios, settings):
    """The DuckDB connection is in service mode: it cannot run a query as alice, so it refuses."""
    alice = helios.as_caller(Caller("alice"))
    assert alice.caller == Caller("alice") and alice.models() == helios.models()
    with pytest.raises(HeliosError, match="data_authorization_denied"):
        alice.query(["Revenue"])
    with pytest.raises(HeliosError, match="data_authorization_denied"):
        Helios.from_settings(settings, caller=Caller("alice")).query(["Revenue"])
    assert helios.query(["Revenue"]).rows                                          # the program itself still can
    assert helios.as_caller(None).query(["Revenue"]).rows


def test_a_hosts_own_data_policy_is_kept(settings):
    class Platform:
        def can_access(self, caller, resource, action):
            assert resource.identity_mode is IdentityMode.SERVICE
            return PlatformDataDecision.allow("the host vouches for its callers")

    policy = DataPolicy(Platform())
    helios = Helios.from_settings(settings, caller=Caller("alice"), data_policy=policy)
    helios.publish(RUN, accept_pending=True)
    assert helios.query(["Revenue"]).rows
    assert helios.as_caller(Caller("bob")).stores.data_policy is policy
    published = helios.stores.registry.semantic_revisions("sales")[0]
    assert published.published_by == "alice"


def test_health_and_tools(helios):
    assert helios.health()["status"] == "healthy" and helios.health()["model_count"] == 1
    assert {"list_models", "run_query"} <= set(helios.tools)


def test_from_settings_reads_a_config_file(settings, tmp_path):
    config = tmp_path / "helios.toml"
    config.write_text(f'data_dir = "{settings.data_dir}"\n\n[connections.default]\nengine = "duckdb"\n'
                      f'path = "{settings.connections["default"].options["path"]}"\n')
    helios = Helios.from_settings(config=config)
    assert helios.settings.data_dir == settings.data_dir
    helios.publish(RUN, accept_pending=True)
    assert helios.query(["Revenue"]).rows
    with pytest.raises(TypeError, match="not both"):
        Helios.from_settings(settings, config=config)
