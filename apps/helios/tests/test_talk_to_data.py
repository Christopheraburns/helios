import json
import time
from types import SimpleNamespace

import httpx2
import pytest
import impala.dbapi as impala_dbapi
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from apps.console import conversation as conversation_module
from apps.console.conversation import (
    ConversationService,
    ConversationUnavailable,
    MCPClientConfig,
    _nested_exception,
)
from apps.console.main import app
from apps.console.mcp_settings import (
    SessionMCPSettingsStore,
    environment_max_tool_rounds,
)
from apps.console.model_provider import (
    LITELLM_GATEWAY_URL,
    SessionModelProvider,
    SessionModelProviderStore,
    llm_for_settings,
    provider_availability,
)
from apps.mcp import server as mcp_server
from helios_core import audit, authz
from helios_core.config import ImpalaConfig
from helios_core.compiler import (
    Compiler,
    Filter,
    FilterValueTypeError,
    SemanticRequest,
)
from helios_core.data_authorization import (
    DataPolicy,
    PlatformDataDecision,
)
from helios_core.delegation import (
    DelegationError,
    issue_assertion,
    verify_assertion,
)
from helios_core.engines.impala import (
    ImpalaAuthenticationError,
    ImpalaEngine,
)
from helios_core.llm import (
    LLMClient,
    LLMTimeoutError,
    ToolCall,
    ToolTurn,
    llm_from_env,
)
from helios_core.llm import client as llm_module
from helios_core.domain import (
    DataSource,
    DataSourceReference,
    Model,
    Organization,
)
from helios_core.metadata import (
    ConversationVersionConflict,
    PrincipalRecord,
    SQLiteMetadataRepository,
    TraceRun,
)
from helios_core.ossie import SemanticModel, build as build_ossie
from helios_core.tracing import TraceRecorder


SECRET = "test-delegation-secret-that-is-at-least-32-bytes"


def principal(subject="owner"):
    return authz.Principal(
        "cloudera-workbench",
        subject,
        authz.PrincipalKind.HUMAN,
        subject.title(),
    )


def fresh_mcp_app(monkeypatch):
    application = mcp_server.server.streamable_http_app(
        transport_security=mcp_server._security,
        stateless_http=True,
        json_response=True,
    )
    monkeypatch.setattr(mcp_server, "_mcp_app", application)
    return application


def draft_model():
    return {
        "model_id": "customer360",
        "datasets": [
            {
                "table": "crm.customers",
                "name": "Customers",
                "kind": "dimension",
                "description": "Customer records",
                "primary_key": ["customer_id"],
                "confidence": 1,
                "fields": [
                    {
                        "column": "region",
                        "name": "Region",
                        "role": "dimension",
                        "description": "Customer region",
                    },
                    {
                        "column": "customer_id",
                        "name": "Customer ID",
                        "role": "identifier",
                        "description": "Identifier",
                    },
                ],
            }
        ],
        "metrics": [
            {
                "name": "Customer count",
                "dataset": "crm.customers",
                "expression": "COUNT(customer_id)",
                "description": "Number of customers",
            }
        ],
        "relationships": [],
        "glossary_terms": [],
    }


def canonical_model() -> SemanticModel:
    document, problems = build_ossie(
        draft_model(),
        "Customer 360",
        "Customer semantics",
        model_id="customer360",
    )
    assert problems == []
    return SemanticModel(document)


def test_compiler_coerces_filter_values_using_semantic_datatype():
    model = canonical_model()
    model.datasets["customers"].fields["customer_id"].datatype = "BIGINT"

    compiled = Compiler(model).compile(
        SemanticRequest(
            metrics=["Customer count"],
            filters=[
                Filter("crm.customers.customer_id", "=", "2002")
            ],
        ),
        dialect="hive",
    )

    assert "customers.customer_id = 2002" in compiled.sql
    assert "'2002'" not in compiled.sql


def test_compiler_rejects_unsafe_filter_type_before_impala():
    model = canonical_model()
    model.datasets["customers"].fields["customer_id"].datatype = "BIGINT"

    with pytest.raises(
        FilterValueTypeError,
        match="expects BIGINT.*without quotes",
    ):
        Compiler(model).compile(
            SemanticRequest(
                metrics=["Customer count"],
                filters=[
                    Filter(
                        "crm.customers.customer_id",
                        "=",
                        "2001-10-01",
                    )
                ],
            ),
            dialect="hive",
        )


def test_impala_analysis_type_mismatch_is_not_service_unavailable():
    hive_server_error = type("HiveServer2Error", (Exception,), {})
    error = hive_server_error(
        "AnalysisException: operands of type BIGINT and STRING "
        "are not comparable"
    )

    message = mcp_server._impala_filter_type_error(error)

    assert message is not None
    assert "value type" in message
    assert mcp_server._impala_filter_type_error(
        RuntimeError("connection timed out")
    ) is None
    assert conversation_module._user_facing_tool_error({
        "error": "invalid_filter_value_type",
        "message": "d_year expects BIGINT",
    }) == "Query filter issue: d_year expects BIGINT"


def test_signed_delegation_round_trip_and_tamper_rejection():
    token = issue_assertion(
        SECRET,
        principal(),
        "acme",
        "customer360",
        now=100,
        request_id="request-123",
        session_id="session-456",
        trace_run_id="trace-789",
        semantic_revision_id="a" * 64,
    )
    context = verify_assertion(SECRET, token, now=110)

    assert context.principal.id == "cloudera-workbench:owner"
    assert context.organization_id == "acme"
    assert context.model_id == "customer360"
    assert context.request_id == "request-123"
    assert context.session_id == "session-456"
    assert context.trace_run_id == "trace-789"
    assert context.semantic_revision_id == "a" * 64
    with pytest.raises(DelegationError):
        verify_assertion(SECRET, token[:-1] + "x", now=110)
    with pytest.raises(DelegationError, match="expired"):
        verify_assertion(SECRET, token, now=200)


def test_mcp_model_store_keeps_the_turns_pinned_revision(
    tmp_path, monkeypatch
):
    artifacts = mcp_server.ArtifactStore(tmp_path)
    first, problems = build_ossie(
        draft_model(),
        "Customer model",
        "first definition",
        model_id="customer360",
    )
    assert problems == []
    first_manifest = {"published_at": "2026-09-28T10:00:00+00:00"}
    artifacts.write_published_ossie(
        "customer360",
        first,
        "name: Customer model\ndescription: first definition\n",
        first_manifest,
    )
    pinned_revision = first_manifest["revision_id"]
    second = {**first, "description": "second definition"}
    artifacts.write_published_ossie(
        "customer360",
        second,
        "name: Customer model\ndescription: second definition\n",
        {"published_at": "2026-09-28T11:00:00+00:00"},
    )
    monkeypatch.setattr(mcp_server, "artifact_store", artifacts)
    store = mcp_server.ModelStore()
    token = mcp_server._caller_context.set(
        mcp_server.MCPCaller(
            principal(),
            "customer360",
            "acme",
            semantic_revision_id=pinned_revision,
        )
    )
    try:
        source, loaded = store.load("customer360")
    finally:
        mcp_server._caller_context.reset(token)

    assert source["revision_id"] == pinned_revision
    assert loaded.description == "first definition"


@pytest.mark.anyio
async def test_mcp_middleware_records_server_ground_truth(tmp_path, monkeypatch):
    repository = SQLiteMetadataRepository(tmp_path / "trace.db")
    repository.migrate()
    repository.save_organization(Organization("acme", "Acme"), "acme")
    repository.save_principal(
        PrincipalRecord(
            id=principal().id,
            external_identity=principal().subject,
            display_name=principal().display_name,
        )
    )
    repository.save_data_source(
        DataSource("warehouse", "acme", "Warehouse", "impala", "connection")
    )
    repository.save_model(
        Model(
            "customer360",
            "acme",
            "Customer 360",
            (DataSourceReference("warehouse"),),
        ),
        created_by=principal().id,
    )
    now = mcp_server.utcnow()
    repository.create_trace_run(
        TraceRun(
            id="trace-1",
            principal_id=principal().id,
            organization_id="acme",
            model_id="customer360",
            purpose="conversation",
            question="Count customers",
            llm_provider="anthropic",
            llm_model="haiku",
            prompt_version="talk-v1",
            status="running",
            started_at=now,
        )
    )
    monkeypatch.setattr(mcp_server, "metadata_repository", repository)
    context = SimpleNamespace(
        method="tools/call",
        request_id="request-1",
        params={
            "name": "describe",
            "arguments": {"name": "customers"},
            "_meta": {
                "helios_trace_run_id": "trace-1",
                "helios_parent_span_id": "client-span-1",
                "helios_sequence": 2,
            },
        },
    )

    async def call_next(_context):
        return {"ok": True}

    result = await mcp_server.MCPTraceMiddleware()(context, call_next)

    spans = repository.trace_spans("trace-1")
    assert result == {"ok": True}
    assert len(spans) == 1
    assert spans[0].component == "mcp-server"
    assert spans[0].parent_span_id == "client-span-1"
    assert spans[0].input["arguments"] == {"name": "customers"}


def test_nested_conversation_failure_is_not_mislabeled_as_mcp_outage():
    expected = ConversationUnavailable("The conversation LLM is unavailable")
    grouped = ExceptionGroup("stream cleanup", [RuntimeError("closed"), expected])

    assert _nested_exception(grouped, ConversationUnavailable) is expected


def test_mistral_environment_uses_server_side_openai_compatible_client(
    monkeypatch,
):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("MISTRAL_API_KEY", "server-side-mistral-secret")

    client = llm_from_env()

    assert client is not None
    assert client.provider == "mistral"
    assert client.model == "mistral-small-latest"
    assert client.base_url == "https://api.mistral.ai/v1"
    assert client.api_key == "server-side-mistral-secret"


def test_mistral_tool_turn_uses_openai_compatible_endpoint(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "search_semantics",
                                        "arguments": '{"question":"orders"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 21, "completion_tokens": 7},
            }

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr("helios_core.llm.client.httpx.post", post)
    client = LLMClient(
        "mistral",
        "mistral-small-latest",
        "server-side-secret",
        "https://api.mistral.ai/v1",
    )

    turn = client.tool_turn(
        "Use MCP.",
        [{"role": "user", "content": "Find orders"}],
        [
            {
                "name": "search_semantics",
                "description": "Search",
                "inputSchema": {
                    "type": "object",
                    "properties": {"question": {"type": "string"}},
                },
            }
        ],
    )

    assert captured["url"] == "https://api.mistral.ai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer server-side-secret"
    assert captured["json"]["model"] == "mistral-small-latest"
    assert turn.tool_calls[0].name == "search_semantics"
    assert turn.stop_reason == "tool_calls"
    assert (turn.tokens_in, turn.tokens_out) == (21, 7)
    assert turn.latency_ms is not None
    assert turn.raw_tool_calls[0]["function"]["name"] == "search_semantics"


def test_llm_client_reports_provider_timeout(monkeypatch):
    def post(*_args, **_kwargs):
        raise llm_module.httpx.ConnectTimeout("connection timed out")

    monkeypatch.setattr("helios_core.llm.client.httpx.post", post)
    client = LLMClient(
        "openai",
        "slow-model",
        "secret",
        "https://provider.example/v1",
        timeout=0.25,
    )

    with pytest.raises(
        LLMTimeoutError,
        match=r"provider timed out \(response limit: 0.25 seconds\)",
    ):
        client.tool_turn("Use MCP.", [], [])


def test_bedrock_tool_turn_uses_bearer_key_and_converse_contract(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "output": {
                    "message": {
                        "content": [
                            {"text": "Checking."},
                            {
                                "toolUse": {
                                    "toolUseId": "call-1",
                                    "name": "describe_model",
                                    "input": {"model": "customer360"},
                                }
                            },
                        ]
                    }
                },
                "stopReason": "tool_use",
                "usage": {"inputTokens": 34, "outputTokens": 9},
            }

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr(llm_module.httpx, "post", post)
    client = LLMClient(
        "bedrock",
        "amazon.nova-pro-v1:0",
        "bedrock-session-key",
        "https://bedrock-runtime.us-east-1.amazonaws.com",
    )

    turn = client.tool_turn(
        "Use tools.",
        [{"role": "user", "content": "Describe the model."}],
        [{
            "name": "describe_model",
            "description": "Describe a model",
            "inputSchema": {"type": "object"},
        }],
    )

    assert captured["url"].endswith(
        "/model/amazon.nova-pro-v1%3A0/converse"
    )
    assert captured["headers"]["Authorization"] == "Bearer bedrock-session-key"
    assert captured["json"]["toolConfig"]["tools"][0]["toolSpec"]["name"] == (
        "describe_model"
    )
    assert turn.tool_calls == (
        ToolCall("call-1", "describe_model", {"model": "customer360"}),
    )
    assert turn.stop_reason == "tool_use"
    assert (turn.tokens_in, turn.tokens_out) == (34, 9)
    assert turn.latency_ms is not None
    assert turn.raw_tool_calls[0]["name"] == "describe_model"


def test_conversation_repository_persists_owned_model_history(
    persistent_auth_stack,
):
    repository = persistent_auth_stack.repository
    created = repository.create_conversation(
        "customer360",
        principal().id,
        "Customers by region",
        "How many customers are in each region?",
        "There are 12 customers in the result.",
    )

    assert repository.schema_version() == 7
    assert created.version == 1
    assert [message.role for message in created.messages] == [
        "user",
        "assistant",
    ]
    assert repository.conversation_for_principal(
        created.id, "customer360", "cloudera-workbench:viewer"
    ) is None
    assert repository.conversation_for_principal(
        created.id, "finance", principal().id
    ) is None

    updated = repository.append_conversation_turn(
        created.id,
        "customer360",
        principal().id,
        1,
        "Which region is largest?",
        "The west region is largest.",
    )

    assert updated.version == 2
    assert [message.content for message in updated.messages] == [
        "How many customers are in each region?",
        "There are 12 customers in the result.",
        "Which region is largest?",
        "The west region is largest.",
    ]
    with pytest.raises(ConversationVersionConflict):
        repository.append_conversation_turn(
            created.id,
            "customer360",
            principal().id,
            1,
            "Stale question",
            "Stale answer",
        )


@pytest.mark.anyio
async def test_mcp_transport_requires_bearer_and_valid_delegation(monkeypatch):
    monkeypatch.setattr(mcp_server, "_DELEGATION_SECRET", SECRET)
    monkeypatch.setattr(mcp_server.store, "sources", lambda: [])
    assertion = issue_assertion(
        SECRET, principal(), "acme", "customer360"
    )
    wire_app = fresh_mcp_app(monkeypatch)
    transport = httpx2.ASGITransport(app=mcp_server.app)
    async with wire_app.router.lifespan_context(wire_app):
        async with httpx2.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1",
        ) as raw_client:
            monkeypatch.setattr(mcp_server, "_TOKEN", None)
            unconfigured = await raw_client.post("/mcp", json={})
            monkeypatch.setattr(mcp_server, "_TOKEN", "transport-secret")
            missing = await raw_client.post("/mcp", json={})
            invalid = await raw_client.post(
                "/mcp",
                headers={
                    "Authorization": "Bearer transport-secret",
                    "X-Helios-Principal-Assertion": assertion + "x",
                },
                json={},
            )
        assert unconfigured.status_code == 503
        assert missing.status_code == 401
        assert invalid.status_code == 401

        async with httpx2.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1",
            headers={
                "Authorization": "Bearer transport-secret",
                "X-Helios-Principal-Assertion": assertion,
            },
        ) as mcp_client:
            async with streamable_http_client(
                "http://127.0.0.1/mcp", http_client=mcp_client
            ) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    response = await session.call_tool("list_models", {})

    assert "run_query" in {tool.name for tool in listed.tools}
    assert response.structured_content == {"result": []}


class AllowDelegatedPlatform:
    def can_access(self, principal, resource, action):
        return PlatformDataDecision.allow("delegated by Impala")


def test_mcp_tools_lock_model_and_propagate_principal(
    persistent_auth_stack,
    monkeypatch,
):
    monkeypatch.setattr(
        mcp_server, "metadata_repository", persistent_auth_stack.repository
    )
    monkeypatch.setattr(
        mcp_server.store,
        "load",
        lambda model=None: (
            {"name": model, "status": "published"},
            canonical_model(),
        ),
    )
    token = mcp_server._caller_context.set(
        mcp_server.MCPCaller(principal(), "customer360", "acme")
    )
    try:
        described = mcp_server.describe_model("customer360")
        denied = mcp_server.describe_model("finance")
        compiled = mcp_server.compile_query(
            ["Customer count"],
            ["crm.customers.region"],
            model="customer360",
        )
    finally:
        mcp_server._caller_context.reset(token)

    assert described["model"] == "customer360"
    assert denied["error"] == "authorization_denied"
    assert compiled["dataset"] == "crm.customers"
    assert "GROUP BY" in compiled["sql"]
    events, total = persistent_auth_stack.repository.audit_events(
        component="mcp",
        offset=0,
        limit=20,
    )
    assert total == 3
    assert {item.action for item in events} == {
        "describe_model",
        "compile_query",
    }
    assert {item.outcome for item in events} == {"success", "denied"}


def test_mcp_tools_load_published_ossie_artifact(
    persistent_auth_stack,
    monkeypatch,
):
    monkeypatch.setattr(
        mcp_server, "artifact_store", persistent_auth_stack.artifacts
    )
    monkeypatch.setattr(
        mcp_server,
        "metadata_repository",
        persistent_auth_stack.repository,
    )
    published_store = mcp_server.ModelStore()
    monkeypatch.setattr(mcp_server, "store", published_store)
    context_token = mcp_server._caller_context.set(
        mcp_server.MCPCaller(principal(), "customer360", "acme")
    )
    try:
        described = mcp_server.describe_model("customer360")
        searched = mcp_server.search_semantics(
            "customer count", model="customer360"
        )
        field_matches = mcp_server.search_semantics(
            "customer id", model="customer360"
        )
        field = mcp_server.describe(
            "warehouse.customers.customer_id",
            model="customer360",
        )
        compiled = mcp_server.compile_query(
            ["customer_count"],
            ["warehouse.customers.customer_id"],
            model="customer360",
        )
        invalid = mcp_server.compile_query(
            ["missing_metric"],
            model="customer360",
        )
        invalid_filter = mcp_server.compile_query(
            ["customer_count"],
            filters=[{
                "column": "warehouse.customers.customer_id",
                "op": "=",
                "value": 12,
            }],
            model="customer360",
        )
        missing = mcp_server.describe(
            "not-a-semantic-object",
            model="customer360",
        )
    finally:
        mcp_server._caller_context.reset(context_token)

    assert described["datasets"][0]["table"] == "warehouse.customers"
    assert searched["matches"][0]["kind"] in {"dataset", "metric", "field"}
    assert any(
        item.get("datatype") == "String"
        for item in field_matches["matches"]
        if item["kind"] == "field"
    )
    assert field["column"] == "customer_id"
    assert compiled["dataset"] == "warehouse.customers"
    assert "GROUP BY" in compiled["sql"]
    assert invalid["error"] == "invalid_semantic_query"
    assert invalid["retryable"] is False
    assert invalid_filter["error"] == "invalid_filter_value_type"
    assert "expects STRING" in invalid_filter["message"]
    assert missing == {
        "error": "semantic_not_found",
        "message": (
            "nothing named 'not-a-semantic-object' is available "
            "in the authorized model"
        ),
        "retryable": False,
    }


def test_mcp_compiles_repository_tpcds_ossie():
    _, model = mcp_server.ModelStore().load("tpcds")

    searched = mcp_server.search(model, "store sales revenue", 5)
    compiled, assets = mcp_server._compile_semantic(
        model,
        ["Store Sales Revenue"],
        ["tpcds.date_dim.d_year"],
        [{"column": "tpcds.date_dim.d_year", "op": "=", "value": 2001}],
        20,
        "impala",
    )

    assert any(
        item["kind"] == "metric" for item in searched["matches"]
    )
    assert compiled["dataset"] == "tpcds.store_sales"
    assert assets == {"tpcds.store_sales", "tpcds.date_dim"}
    assert "store_sales_revenue" in compiled["columns"]
    assert "WHERE" in compiled["sql"]


def test_mcp_model_store_converts_proposal_fallback(
    tmp_path,
    monkeypatch,
):
    artifacts = mcp_server.ArtifactStore(tmp_path / "artifacts")
    runs = tmp_path / "runs"
    run = runs / "run-1"
    run.mkdir(parents=True)
    (run / "propose.json").write_text(json.dumps(draft_model()))
    monkeypatch.setattr(mcp_server, "artifact_store", artifacts)
    monkeypatch.setattr(mcp_server.runstore, "RUNS_DIR", str(runs))
    monkeypatch.setattr(
        mcp_server.runstore,
        "list_runs",
        lambda: [{"id": "run-1", "stages": {"propose": True}}],
    )

    source, model = mcp_server.ModelStore().load("customer360")

    assert artifacts.published_model_ids() == []
    assert source["status"] == "proposed (unreviewed)"
    assert model.datasets["customers"].source == "crm.customers"
    assert model.metric("Customer count").dataset == "customers"


def test_mcp_run_query_uses_delegated_identity(
    persistent_auth_stack,
    monkeypatch,
):
    monkeypatch.setattr(
        mcp_server, "metadata_repository", persistent_auth_stack.repository
    )
    monkeypatch.setattr(
        mcp_server.store,
        "load",
        lambda model=None: (
            {"name": model, "status": "published"},
            canonical_model(),
        ),
    )
    monkeypatch.setattr(
        mcp_server,
        "data_policy",
        DataPolicy(AllowDelegatedPlatform()),
    )
    monkeypatch.setattr(
        mcp_server,
        "impala_config",
        lambda: ImpalaConfig(
            "warehouse.example",
            443,
            "helios-service",
            "secret",
            proxy_delegation=True,
        ),
    )
    captured = {}

    def query(_self, sql, limit, *, delegated_user=None):
        captured.update(
            sql=sql, limit=limit, delegated_user=delegated_user
        )
        return SimpleNamespace(columns=["customer_count"], rows=[(12,)])

    monkeypatch.setattr(ImpalaEngine, "query", query)
    context_token = mcp_server._caller_context.set(
        mcp_server.MCPCaller(principal(), "customer360", "acme")
    )
    try:
        invalid = mcp_server.run_query(
            ["total_store_sales"],
            ["item.item_category"],
            model="customer360",
        )
        invalid_field = mcp_server.run_query(
            ["Customer count"],
            ["item.item_category"],
            model="customer360",
        )
        result = mcp_server.run_query(
            ["Customer count"], limit=5000, model="customer360"
        )
    finally:
        mcp_server._caller_context.reset(context_token)

    assert invalid["error"] == "invalid_semantic_query"
    assert "unknown metric" in invalid["message"]
    assert "search_semantics" in invalid["message"]
    assert invalid_field["error"] == "invalid_semantic_query"
    assert "unknown field item.item_category" in invalid_field["message"]
    assert result["rows"] == [[12]]
    assert captured["delegated_user"] == "owner"
    assert captured["limit"] == 1000

    hive_server_error = type("HiveServer2Error", (Exception,), {})

    def analysis_mismatch(
        _self, _sql, _limit=1000, *, delegated_user=None
    ):
        raise hive_server_error(
            "AnalysisException: operands of type BIGINT and STRING "
            "are not comparable"
        )

    monkeypatch.setattr(ImpalaEngine, "query", analysis_mismatch)
    context_token = mcp_server._caller_context.set(
        mcp_server.MCPCaller(principal(), "customer360", "acme")
    )
    try:
        mismatch = mcp_server.run_query(
            ["Customer count"], model="customer360"
        )
    finally:
        mcp_server._caller_context.reset(context_token)
    assert mismatch["error"] == "invalid_filter_value_type"
    assert mismatch["retryable"] is False

    def ranger_denied(_self, _sql, _limit, *, delegated_user=None):
        raise PermissionError(f"Ranger denied {delegated_user}")

    monkeypatch.setattr(ImpalaEngine, "query", ranger_denied)
    context_token = mcp_server._caller_context.set(
        mcp_server.MCPCaller(principal(), "customer360", "acme")
    )
    try:
        denied = mcp_server.run_query(
            ["Customer count"], model="customer360"
        )
    finally:
        mcp_server._caller_context.reset(context_token)
    assert denied["error"] == "query_denied"
    assert "Ranger denied owner" in denied["message"]

    def proxy_rejected(_self, _sql, _limit=1000, *, delegated_user=None):
        if delegated_user:
            raise ImpalaAuthenticationError("delegated HTTP 401")
        return SimpleNamespace(columns=["1"], rows=[(1,)])

    monkeypatch.setattr(ImpalaEngine, "query", proxy_rejected)
    context_token = mcp_server._caller_context.set(
        mcp_server.MCPCaller(principal(), "customer360", "acme")
    )
    try:
        rejected = mcp_server.run_query(
            ["Customer count"], model="customer360"
        )
    finally:
        mcp_server._caller_context.reset(context_token)
    assert rejected["error"] == "proxy_delegation_denied"
    assert not rejected["retryable"]
    assert "_audit_diagnostics" not in rejected

    def workload_rejected(_self, _sql, _limit=1000, *, delegated_user=None):
        raise ImpalaAuthenticationError("service HTTP 401")

    monkeypatch.setattr(ImpalaEngine, "query", workload_rejected)
    context_token = mcp_server._caller_context.set(
        mcp_server.MCPCaller(principal(), "customer360", "acme")
    )
    try:
        rejected = mcp_server.run_query(
            ["Customer count"], model="customer360"
        )
    finally:
        mcp_server._caller_context.reset(context_token)
    assert rejected["error"] == "workload_authentication_failed"
    assert "username or password" in rejected["message"]
    assert not rejected["retryable"]

    monkeypatch.setenv("WORKLOAD_PASSWORD", "private-password")

    def warehouse_unavailable(
        _self, sql, _limit, *, delegated_user=None
    ):
        raise RuntimeError(
            f"connection failed with private-password while running {sql}"
        )

    monkeypatch.setattr(ImpalaEngine, "query", warehouse_unavailable)
    context_token = mcp_server._caller_context.set(
        mcp_server.MCPCaller(principal(), "customer360", "acme")
    )
    audit_token = audit.set_context(
        audit.AuditContext(
            request_id="request-query-failure",
            principal_id=principal().id,
            organization_id="acme",
            model_id="customer360",
        )
    )
    try:
        unavailable = mcp_server.run_query(
            ["Customer count"], model="customer360"
        )
    finally:
        audit.reset_context(audit_token)
        mcp_server._caller_context.reset(context_token)

    assert unavailable["error"] == "query_unavailable"
    assert "_audit_diagnostics" not in unavailable
    events, _ = persistent_auth_stack.repository.audit_events(
        principal_id=principal().id,
        component="mcp",
        offset=0,
        limit=20,
    )
    failed = next(
        event
        for event in events
        if event.details.get("error_code") == "query_unavailable"
    )
    rendered = str(failed.details["diagnostics"])
    assert failed.details["diagnostics"]["stage"] == "impala_query"
    assert "RuntimeError" in rendered
    assert "private-password" not in rendered
    assert "SELECT" not in rendered


class FakeCursor:
    def __init__(self, effective_user):
        self.effective_user = effective_user
        self.description = None
        self.executed = []

    def execute(self, sql):
        self.executed.append(sql)
        if sql == "SELECT EFFECTIVE_USER()":
            self.description = [("effective_user",)]
        else:
            self.description = [("value",)]

    def fetchone(self):
        return (self.effective_user,)

    def fetchmany(self, _limit):
        return [(7,)]


class FakeConnection:
    def __init__(self, effective_user):
        self.cursor_value = FakeCursor(effective_user)

    def cursor(self):
        return self.cursor_value

    def close(self):
        pass


class HttpUnauthorized(Exception):
    code = 401


class FailedOpenConnection:
    def cursor(self):
        raise HttpUnauthorized("HTTP code 401: Unauthorized")

    def close(self):
        raise AttributeError("'NoneType' object has no attribute 'close'")


def test_impala_preserves_http_authentication_error_when_cleanup_fails(
    monkeypatch,
):
    monkeypatch.setattr(
        impala_dbapi,
        "connect",
        lambda **_kwargs: FailedOpenConnection(),
    )
    engine = ImpalaEngine(
        ImpalaConfig(
            "warehouse.example",
            443,
            "workload-user",
            "secret",
        )
    )

    with pytest.raises(ImpalaAuthenticationError) as captured:
        engine.ping()

    assert isinstance(captured.value.__cause__, HttpUnauthorized)
    assert "workload authentication" in str(captured.value)


def test_impala_proxy_delegation_verifies_effective_user(monkeypatch):
    captured = {}

    def connect(**kwargs):
        captured.update(kwargs)
        return FakeConnection("owner")

    monkeypatch.setattr(
        impala_dbapi, "connect", connect
    )
    engine = ImpalaEngine(
        ImpalaConfig(
            "warehouse.example",
            443,
            "helios-service",
            "secret",
            http_path="cliservice",
            proxy_delegation=True,
        )
    )
    result = engine.query("SELECT 7", delegated_user="owner")

    assert result.rows == [(7,)]
    assert "doAs=owner" in captured["http_path"]


def test_impala_proxy_delegation_stops_on_identity_mismatch(monkeypatch):
    connection = FakeConnection("somebody-else")
    monkeypatch.setattr(
        impala_dbapi,
        "connect",
        lambda **_kwargs: connection,
    )
    engine = ImpalaEngine(
        ImpalaConfig(
            "warehouse.example",
            443,
            "helios-service",
            "secret",
            proxy_delegation=True,
        )
    )

    with pytest.raises(PermissionError, match="delegated SSO identity"):
        engine.query("SELECT secret", delegated_user="owner")
    assert connection.cursor_value.executed == ["SELECT EFFECTIVE_USER()"]


class FakeLLM:
    def __init__(self):
        self.turn = 0
        self.provider = "fake"
        self.model = "fake"

    def tool_turn(self, _system, _messages, _tools):
        self.turn += 1
        if self.turn == 1:
            return ToolTurn(
                "",
                (
                    ToolCall(
                        "call-1",
                        "search_semantics",
                        {"question": "customers by region", "model": "finance"},
                    ),
                ),
                "tool_use",
            )
        if self.turn == 2:
            return ToolTurn(
                "",
                (
                    ToolCall(
                        "call-2",
                        "run_query",
                        {
                            "metrics": ["Customer count"],
                            "dimensions": ["crm.customers.region"],
                        },
                    ),
                ),
                "tool_use",
            )
        return ToolTurn("There are 12 customers in the result.", (), "stop")


class FakeSession:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments, meta=None):
        del meta
        self.calls.append((name, arguments))
        result = (
            {"matches": [{"kind": "metric", "name": "Customer count"}]}
            if name == "search_semantics"
            else {
                "sql": "SELECT COUNT(customer_id) FROM crm.customers",
                "columns": ["customer_count"],
                "rows": [[12]],
            }
        )
        return SimpleNamespace(structuredContent=result, content=[])


class HistoryCapturingLLM:
    def __init__(self):
        self.messages = []

    def tool_turn(self, _system, messages, _tools):
        self.messages = messages
        return ToolTurn("History-aware answer", (), "stop")


class RepeatingFailureLLM:
    def __init__(self):
        self.turns = 0

    def tool_turn(self, _system, _messages, _tools):
        self.turns += 1
        return ToolTurn(
            "",
            (
                ToolCall(
                    f"call-{self.turns}",
                    "describe",
                    {"name": "missing"},
                ),
            ),
            "tool_use",
        )


class NonRetryableFailureSession:
    def __init__(self):
        self.calls = 0

    async def call_tool(self, name, arguments):
        del name, arguments
        self.calls += 1
        return SimpleNamespace(
            structured_content={
                "result": {
                    "error": "semantic_not_found",
                    "message": "The requested semantic object was not found.",
                    "retryable": False,
                }
            },
            content=[],
        )


@pytest.mark.anyio
async def test_conversation_loop_uses_discovered_mcp_tools_and_locks_model():
    service = ConversationService(
        FakeLLM(),
        MCPClientConfig("https://mcp.example", "token", SECRET),
    )
    session = FakeSession()
    tools = [
        {
            "name": "search_semantics",
            "description": "Search",
            "inputSchema": {
                "properties": {"question": {}, "model": {}}
            },
        },
        {
            "name": "run_query",
            "description": "Run",
            "inputSchema": {
                "properties": {
                    "metrics": {},
                    "dimensions": {},
                    "model": {},
                }
            },
        },
    ]

    result = await service._tool_loop(
        session, tools, "customer360", "How many customers by region?"
    )

    assert result["answer"] == "There are 12 customers in the result."
    assert result["query_result"]["rows"] == [[12]]
    assert session.calls[0][1]["model"] == "customer360"
    assert session.calls[1][1]["model"] == "customer360"


@pytest.mark.anyio
async def test_conversation_ends_slow_provider_request(
    persistent_auth_stack,
):
    class SlowLLM:
        provider = "fake"
        model = "slow-model"
        timeout = 0.01

        def tool_turn(self, _system, _messages, _tools):
            time.sleep(0.1)
            return ToolTurn("Too late", (), "stop")

    service = ConversationService(
        SlowLLM(),
        MCPClientConfig("https://mcp.example", "token", SECRET),
    )
    recorder = TraceRecorder.start(
        persistent_auth_stack.repository,
        principal_id=principal().id,
        organization_id="acme",
        model_id="customer360",
        question="How many customers?",
        provider="fake",
        llm_model="slow-model",
        request_id="request-timeout",
    )

    with pytest.raises(
        ConversationUnavailable,
        match="model provider took too long to respond",
    ):
        await service._tool_loop(
            FakeSession(),
            [],
            "customer360",
            "How many customers?",
            recorder=recorder,
        )
    run = persistent_auth_stack.repository.trace_run(recorder.run.id)
    assert run is not None
    assert run.status == "failed"
    assert run.termination_reason == "timeout"


@pytest.mark.anyio
async def test_trace_records_tool_round_limit_termination(persistent_auth_stack):
    repository = persistent_auth_stack.repository
    recorder = TraceRecorder.start(
        repository,
        principal_id=principal().id,
        organization_id="acme",
        model_id="customer360",
        question="How many customers?",
        provider="fake",
        llm_model="fake",
        request_id="request-1",
    )
    service = ConversationService(
        FakeLLM(),
        MCPClientConfig("https://mcp.example", "token", SECRET),
        max_tool_rounds=1,
    )

    with pytest.raises(
        ConversationUnavailable,
        match="exceeded the MCP tool-call limit",
    ):
        await service._tool_loop(
            FakeSession(),
            [{
                "name": "search_semantics",
                "description": "Search",
                "inputSchema": {
                    "properties": {"question": {}, "model": {}},
                },
            }],
            "customer360",
            "How many customers?",
            recorder=recorder,
        )

    run = repository.trace_run(recorder.run.id)
    assert run is not None
    assert run.status == "failed"
    assert run.termination_reason == "tool_round_limit"
    assert [span.kind for span in repository.trace_spans(run.id)] == [
        "llm",
        "tool",
    ]


@pytest.mark.anyio
async def test_conversation_stops_repeated_non_retryable_tool_failure():
    llm = RepeatingFailureLLM()
    session = NonRetryableFailureSession()
    service = ConversationService(
        llm,
        MCPClientConfig("https://mcp.example", "token", SECRET),
    )
    tools = [
        {
            "name": "describe",
            "description": "Describe",
            "inputSchema": {
                "properties": {"name": {}, "model": {}}
            },
        }
    ]

    result = await service._tool_loop(
        session, tools, "customer360", "Describe missing"
    )

    assert session.calls == 2
    assert llm.turns == 2
    assert (
        result["answer"]
        == "Semantic model issue: "
        "The requested semantic object was not found."
    )
    assert result["failure"]["code"] == "semantic_not_found"
    assert len(result["tool_trace"]) == 2


@pytest.mark.anyio
async def test_conversation_does_not_mask_semantic_error_as_impala_failure():
    class MaskingLLM:
        provider = "mistral"
        model = "mistral-small-latest"

        def __init__(self):
            self.turns = 0
            self.system = ""
            self.messages = []

        def tool_turn(self, system, messages, _tools):
            self.turns += 1
            self.system = system
            self.messages = messages
            if self.turns == 1:
                return ToolTurn(
                    "",
                    (
                        ToolCall(
                            "call-1",
                            "run_query",
                            {
                                "metrics": ["total_store_sales"],
                                "dimensions": ["item.item_category"],
                            },
                        ),
                    ),
                    "tool_use",
                )
            return ToolTurn(
                "There is a temporary issue with the Impala query service.",
                (),
                "stop",
            )

    class InvalidSemanticSession:
        async def call_tool(self, _name, arguments):
            del arguments
            return SimpleNamespace(
                structuredContent={
                    "error": "invalid_semantic_query",
                    "message": (
                        "unknown metric 'total_store_sales'. Use an exact "
                        "metric name from search_semantics and use "
                        "database.table.column identifiers for dimensions "
                        "and filters."
                    ),
                    "retryable": False,
                },
                content=[],
            )

    llm = MaskingLLM()
    service = ConversationService(
        llm,
        MCPClientConfig("https://mcp.example", "token", SECRET),
    )
    result = await service._tool_loop(
        InvalidSemanticSession(),
        [{
            "name": "run_query",
            "description": "Run",
            "inputSchema": {"properties": {"metrics": {}, "dimensions": {}}},
        }],
        "customer360",
        "Show store sales by category",
    )

    assert result["failure"]["code"] == "invalid_semantic_query"
    assert result["answer"].startswith("Semantic model issue:")
    assert "temporary issue with the Impala" not in result["answer"]
    assert "call search_semantics" in llm.system
    tool_result = json.loads(llm.messages[-1]["content"])
    assert "recovery" in tool_result
    assert "database.table.column" in tool_result["recovery"]


@pytest.mark.anyio
async def test_conversation_loop_bounds_persisted_history():
    llm = HistoryCapturingLLM()
    service = ConversationService(
        llm,
        MCPClientConfig("https://mcp.example", "token", SECRET),
    )
    history = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"message-{index}",
        }
        for index in range(30)
    ]

    result = await service._tool_loop(
        FakeSession(),
        [],
        "customer360",
        "current question",
        history=history,
    )

    assert result["answer"] == "History-aware answer"
    assert len(llm.messages) == 21
    assert llm.messages[0]["content"] == "message-10"
    assert llm.messages[-1]["content"] == "current question"


@pytest.mark.anyio
async def test_conversation_turn_uses_real_mcp_transport(
    persistent_auth_stack,
    monkeypatch,
):
    monkeypatch.setattr(
        mcp_server, "metadata_repository", persistent_auth_stack.repository
    )
    monkeypatch.setattr(
        mcp_server.store,
        "load",
        lambda model=None: (
            {"name": model, "status": "published"},
            canonical_model(),
        ),
    )
    monkeypatch.setattr(
        mcp_server,
        "data_policy",
        DataPolicy(AllowDelegatedPlatform()),
    )
    monkeypatch.setattr(
        mcp_server,
        "impala_config",
        lambda: ImpalaConfig(
            "warehouse.example",
            443,
            "helios-service",
            "secret",
            proxy_delegation=True,
        ),
    )
    monkeypatch.setattr(
        ImpalaEngine,
        "query",
        lambda _self, _sql, _limit, *, delegated_user=None: SimpleNamespace(
            columns=["customer_count"], rows=[(12,)]
        ),
    )
    monkeypatch.setattr(mcp_server, "_TOKEN", "transport-secret")
    monkeypatch.setattr(mcp_server, "_DELEGATION_SECRET", SECRET)
    wire_app = fresh_mcp_app(monkeypatch)
    transport = httpx2.ASGITransport(app=mcp_server.app)
    real_async_client = httpx2.AsyncClient

    def in_process_client(*args, **kwargs):
        return real_async_client(
            *args,
            transport=transport,
            base_url="http://127.0.0.1",
            **kwargs,
        )

    monkeypatch.setattr(
        conversation_module.httpx2, "AsyncClient", in_process_client
    )
    service = ConversationService(
        FakeLLM(),
        MCPClientConfig(
            "http://127.0.0.1/mcp",
            "transport-secret",
            SECRET,
        ),
    )
    async with wire_app.router.lifespan_context(wire_app):
        result = await service.turn(
            principal(),
            "acme",
            "customer360",
            "How many customers by region?",
        )

    assert result["answer"] == "There are 12 customers in the result."
    assert result["query_result"]["rows"] == [[12]]
    assert result["tool_trace"][0]["arguments"]["model"] == "customer360"
    assert result["tool_trace"][1]["arguments"]["model"] == "customer360"
    assert result["provenance"]["llm"] == {
        "provider": "fake",
        "model": "fake",
    }
    assert result["provenance"]["mcp"]["server_name"] == "helios"
    assert result["provenance"]["mcp"]["server_version"] == "0.1.0"


class FakeConversationService:
    def __init__(self):
        self.calls = []

    async def turn(
        self,
        principal,
        organization_id,
        model_id,
        message,
        history=None,
    ):
        self.calls.append(
            (principal.id, organization_id, model_id, message, history)
        )
        return {
            "model_id": model_id,
            "answer": "MCP-backed answer",
            "tool_trace": [
                {
                    "tool": "run_query",
                    "arguments": {"model": model_id},
                    "result": {"rows": [["sensitive transient value"]]},
                }
            ],
            "query_result": {
                "columns": ["value"],
                "rows": [["sensitive transient value"]],
                "sql": "SELECT sensitive_value",
            },
            "provenance": {
                "helios": {"api_version": "0.1.0"},
                "llm": {
                    "provider": "mistral",
                    "model": "mistral-small-latest",
                },
                "mcp": {
                    "server_name": "helios",
                    "server_version": "0.1.0",
                    "protocol_version": "2025-11-25",
                },
            },
            "request_id": "request-talk-test",
        }


def test_conversation_endpoint_preserves_authenticated_model_context(
    persistent_auth_stack,
):
    previous = dict(app.state._state)
    service = FakeConversationService()
    app.state._state.pop("resource_store", None)
    app.state._state.pop("authorization_policy", None)
    app.state.metadata_repository = persistent_auth_stack.repository
    app.state.conversation_service = service
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/models/customer360/conversation/turns",
                headers={"x-forwarded-user": "owner"},
                json={"message": "How many customers?"},
            )
            denied = client.post(
                "/api/v1/models/finance/conversation/turns",
                headers={"x-forwarded-user": "owner"},
                json={"message": "Show finance"},
            )
    finally:
        app.state._state.clear()
        app.state._state.update(previous)

    assert response.status_code == 200
    assert response.json()["answer"] == "MCP-backed answer"
    assert service.calls == [
        (
            "cloudera-workbench:owner",
            "acme",
            "customer360",
            "How many customers?",
            None,
        )
    ]
    assert denied.status_code == 403


def test_persistent_conversation_api_restores_history_and_isolates_owner(
    persistent_auth_stack,
):
    previous = dict(app.state._state)
    service = FakeConversationService()
    app.state._state.pop("resource_store", None)
    app.state._state.pop("authorization_policy", None)
    app.state.metadata_repository = persistent_auth_stack.repository
    app.state.conversation_service = service
    try:
        with TestClient(app) as client:
            created_response = client.post(
                "/api/v1/models/customer360/conversations",
                headers={"x-forwarded-user": "owner"},
                json={"message": "How many customers?"},
            )
            created = created_response.json()
            conversation_id = created["conversation"]["id"]
            appended_response = client.post(
                f"/api/v1/models/customer360/conversations/{conversation_id}/turns",
                headers={"x-forwarded-user": "owner"},
                json={
                    "message": "Break that down by region.",
                    "expected_version": 1,
                },
            )
            listed = client.get(
                "/api/v1/models/customer360/conversations",
                headers={"x-forwarded-user": "owner"},
            )
            denied = client.get(
                f"/api/v1/models/customer360/conversations/{conversation_id}",
                headers={"x-forwarded-user": "viewer"},
            )
            stale = client.post(
                f"/api/v1/models/customer360/conversations/{conversation_id}/turns",
                headers={"x-forwarded-user": "owner"},
                json={"message": "Stale", "expected_version": 1},
            )
            detail = client.get(
                f"/api/v1/models/customer360/conversations/{conversation_id}",
                headers={"x-forwarded-user": "owner"},
            )
            archived = client.patch(
                f"/api/v1/models/customer360/conversations/{conversation_id}",
                headers={"x-forwarded-user": "owner"},
                json={"archived": True},
            )
            listed_after_archive = client.get(
                "/api/v1/models/customer360/conversations",
                headers={"x-forwarded-user": "owner"},
            )
            archived_list = client.get(
                "/api/v1/models/customer360/conversations"
                "?include_archived=true",
                headers={"x-forwarded-user": "owner"},
            )
            append_archived = client.post(
                f"/api/v1/models/customer360/conversations/{conversation_id}/turns",
                headers={"x-forwarded-user": "owner"},
                json={"message": "Archived", "expected_version": 3},
            )
            denied_archive = client.patch(
                f"/api/v1/models/customer360/conversations/{conversation_id}",
                headers={"x-forwarded-user": "viewer"},
                json={"archived": True},
            )
    finally:
        app.state._state.clear()
        app.state._state.update(previous)

    assert created_response.status_code == 200
    assert appended_response.status_code == 200
    assert appended_response.json()["conversation"]["version"] == 2
    assert len(appended_response.json()["conversation"]["messages"]) == 4
    assert listed.json()["conversations"][0]["id"] == conversation_id
    assert denied.status_code == 404
    assert stale.status_code == 409
    assert detail.status_code == 200
    persisted_turn = detail.json()["messages"][-1]["turn"]
    assert persisted_turn["query_result"]["rows"] == [
        ["sensitive transient value"]
    ]
    assert persisted_turn["tool_trace"][0]["tool"] == "run_query"
    assert persisted_turn["provenance"]["llm"] == {
        "provider": "mistral",
        "model": "mistral-small-latest",
    }
    assert persisted_turn["provenance"]["mcp"]["server_version"] == "0.1.0"
    assert persisted_turn["request_id"] == "request-talk-test"
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None
    assert listed_after_archive.json()["conversations"] == []
    assert archived_list.json()["conversations"][0]["id"] == conversation_id
    assert append_archived.status_code == 409
    assert denied_archive.status_code == 404
    assert service.calls[1][4] == [
        {"role": "user", "content": "How many customers?"},
        {"role": "assistant", "content": "MCP-backed answer"},
    ]
    stored = persistent_auth_stack.repository.conversation_for_principal(
        conversation_id,
        "customer360",
        principal().id,
    )
    assert stored is not None
    persisted_text = " ".join(message.content for message in stored.messages)
    assert "sensitive transient value" not in persisted_text
    assert "SELECT sensitive_value" not in persisted_text
    assert len(stored.turns) == 2
    assert stored.turns[-1].query_result["rows"] == (
        [["sensitive transient value"]]
    )
    assert stored.turns[-1].provenance["llm"]["provider"] == "mistral"


def test_conversation_endpoint_reports_missing_server_configuration(
    persistent_auth_stack,
    monkeypatch,
):
    for name in (
        "HELIOS_MCP_URL",
        "HELIOS_MCP_TOKEN",
        "HELIOS_MCP_DELEGATION_SECRET",
        "ANTHROPIC_API_KEY",
        "INFERENCE_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    previous = dict(app.state._state)
    app.state._state.pop("resource_store", None)
    app.state._state.pop("authorization_policy", None)
    app.state._state.pop("conversation_service", None)
    app.state.metadata_repository = persistent_auth_stack.repository
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/models/customer360/conversation/turns",
                headers={"x-forwarded-user": "owner"},
                json={"message": "How many customers?"},
            )
    finally:
        app.state._state.clear()
        app.state._state.update(previous)

    assert response.status_code == 503
    assert (
        "MCP conversation connectivity is not configured"
        in response.json()["detail"]
    )


def test_openai_session_provider_uses_litellm_gateway(monkeypatch):
    monkeypatch.delenv("INFERENCE_BASE_URL", raising=False)
    client = llm_for_settings(
        SessionModelProvider("openai", "claude-haiku-4-5", "user-session-secret")
    )

    assert provider_availability()["openai"] is True
    assert client.provider == "openai"
    assert client.model == "claude-haiku-4-5"
    assert client.api_key == "user-session-secret"
    assert client.base_url == LITELLM_GATEWAY_URL

    monkeypatch.setenv("INFERENCE_BASE_URL", "https://inference.example/v1")
    overridden = llm_for_settings(
        SessionModelProvider("openai", "claude-haiku-4-5", "user-session-secret")
    )
    assert overridden.base_url == "https://inference.example/v1"


def test_session_model_provider_override_is_private_and_drives_conversation(
    persistent_auth_stack,
    monkeypatch,
):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "environment-secret")
    monkeypatch.setenv("ANTHROPIC_MODEL", "environment-model")
    monkeypatch.setenv("HELIOS_MCP_URL", "https://mcp.example/mcp")
    monkeypatch.setenv("HELIOS_MCP_TOKEN", "mcp-token")
    monkeypatch.setenv("HELIOS_MCP_DELEGATION_SECRET", SECRET)
    captured = {}

    async def fake_turn(
        self,
        _principal,
        _organization_id,
        model_id,
        _message,
        history=None,
    ):
        captured.update(
            provider=self.llm.provider,
            model=self.llm.model,
            api_key=self.llm.api_key,
        )
        return {
            "model_id": model_id,
            "answer": "Session provider answer",
            "tool_trace": [],
            "query_result": None,
            "provenance": {
                "llm": {
                    "provider": self.llm.provider,
                    "model": self.llm.model,
                }
            },
            "request_id": "provider-request",
        }

    monkeypatch.setattr(ConversationService, "turn", fake_turn)
    previous = dict(app.state._state)
    store = SessionModelProviderStore()
    app.state._state.pop("resource_store", None)
    app.state._state.pop("authorization_policy", None)
    app.state._state.pop("conversation_service", None)
    app.state.metadata_repository = persistent_auth_stack.repository
    app.state.model_provider_settings = store
    headers = {
        "x-forwarded-user": "owner",
        "x-helios-session-id": "3d9d44df-4c14-4c35-8575-d3817589e18a",
    }
    try:
        with TestClient(app) as client:
            initial = client.get(
                "/api/v1/model-provider-settings",
                headers=headers,
            )
            updated = client.put(
                "/api/v1/model-provider-settings",
                headers=headers,
                json={
                    "provider": "mistral",
                    "model": "mistral-large-latest",
                    "api_key": "user-session-secret",
                },
            )
            isolated = client.get(
                "/api/v1/model-provider-settings",
                headers={
                    **headers,
                    "x-helios-session-id":
                        "0d660ec7-0431-4fbb-9142-f520816c5edf",
                },
            )
            rejected_secret = "s" * 10_001
            rejected = client.put(
                "/api/v1/model-provider-settings",
                headers=headers,
                json={
                    "provider": "mistral",
                    "model": "mistral-large-latest",
                    "api_key": rejected_secret,
                },
            )
            conversation = client.post(
                "/api/v1/models/customer360/conversations",
                headers=headers,
                json={"message": "Use my selected model"},
            )
            cleared = client.delete(
                "/api/v1/model-provider-settings",
                headers=headers,
            )
    finally:
        app.state._state.clear()
        app.state._state.update(previous)

    assert initial.json() | {"providers": []} == {
        "source": "environment",
        "provider": "anthropic",
        "model": "environment-model",
        "api_key_configured": False,
        "providers": [],
    }
    assert updated.status_code == 200
    assert updated.json()["source"] == "session"
    assert updated.json()["api_key_configured"] is True
    assert "user-session-secret" not in updated.text
    assert isolated.json()["source"] == "environment"
    assert rejected.status_code == 422
    assert rejected_secret not in rejected.text
    assert conversation.status_code == 200
    assert captured == {
        "provider": "mistral",
        "model": "mistral-large-latest",
        "api_key": "user-session-secret",
    }
    assert cleared.json()["source"] == "environment"
    assert store.get(
        principal().id,
        headers["x-helios-session-id"],
    ) is None


def test_mcp_client_config_reads_validated_timeout_from_environment(
    monkeypatch,
):
    monkeypatch.setenv("HELIOS_MCP_URL", "https://mcp.example/mcp")
    monkeypatch.setenv("HELIOS_MCP_TOKEN", "token")
    monkeypatch.setenv("HELIOS_MCP_DELEGATION_SECRET", SECRET)
    monkeypatch.setenv("HELIOS_MCP_TIMEOUT_SECONDS", "180")

    configuration = MCPClientConfig.from_env()

    assert configuration.timeout_seconds == 180

    for invalid in ("zero", "0", "-1", "nan", "inf"):
        monkeypatch.setenv("HELIOS_MCP_TIMEOUT_SECONDS", invalid)
        with pytest.raises(
            ConversationUnavailable,
            match="HELIOS_MCP_TIMEOUT_SECONDS",
        ):
            MCPClientConfig.from_env()


def test_session_mcp_settings_drive_conversation_and_report_tools(
    persistent_auth_stack,
    monkeypatch,
):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "environment-secret")
    monkeypatch.setenv("ANTHROPIC_MODEL", "environment-model")
    monkeypatch.setenv("HELIOS_MCP_URL", "https://mcp.example/mcp")
    monkeypatch.setenv("HELIOS_MCP_TOKEN", "mcp-token")
    monkeypatch.setenv("HELIOS_MCP_DELEGATION_SECRET", SECRET)
    monkeypatch.setenv("HELIOS_MCP_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("HELIOS_MCP_MAX_TOOL_ROUNDS", "6")
    captured = {}

    async def fake_turn(
        self,
        _principal,
        _organization_id,
        model_id,
        _message,
        history=None,
    ):
        captured["max_tool_rounds"] = self.max_tool_rounds
        return {
            "model_id": model_id,
            "answer": "MCP settings answer",
            "tool_trace": [],
            "query_result": None,
        }

    async def fake_inspector(
        configuration,
        _principal,
        _organization_id,
        _model_id,
    ):
        assert configuration.timeout_seconds == 180
        return {
            "server": {
                "server_name": "helios",
                "server_version": "1.2.3",
                "protocol_version": "2025-11-25",
            },
            "tools": [
                {"name": "run_query", "description": "Run a governed query."},
                {"name": "describe", "description": "Describe an object."},
            ],
        }

    monkeypatch.setattr(ConversationService, "turn", fake_turn)
    previous = dict(app.state._state)
    store = SessionMCPSettingsStore()
    app.state._state.pop("resource_store", None)
    app.state._state.pop("authorization_policy", None)
    app.state._state.pop("conversation_service", None)
    app.state.metadata_repository = persistent_auth_stack.repository
    app.state.mcp_settings = store
    app.state.mcp_inspector = fake_inspector
    headers = {
        "x-forwarded-user": "owner",
        "x-helios-session-id": "4d9d44df-4c14-4c35-8575-d3817589e18a",
    }
    try:
        with TestClient(app) as client:
            initial = client.get("/api/v1/mcp-settings", headers=headers)
            updated = client.put(
                "/api/v1/mcp-settings",
                headers=headers,
                json={"max_tool_rounds": 10},
            )
            status = client.get(
                "/api/v1/models/customer360/mcp-status",
                headers=headers,
            )
            conversation = client.post(
                "/api/v1/models/customer360/conversations",
                headers=headers,
                json={"message": "Use the MCP override"},
            )
            rejected = client.put(
                "/api/v1/mcp-settings",
                headers=headers,
                json={"max_tool_rounds": 21},
            )
            cleared = client.delete("/api/v1/mcp-settings", headers=headers)
    finally:
        app.state._state.clear()
        app.state._state.update(previous)

    assert initial.json()["max_tool_rounds"] == 6
    assert initial.json()["source"] == "environment"
    assert updated.json()["max_tool_rounds"] == 10
    assert updated.json()["source"] == "session"
    assert status.json()["status"] == "available"
    assert status.json()["timeout_seconds"] == 180
    assert [tool["name"] for tool in status.json()["tools"]] == [
        "describe",
        "run_query",
    ]
    assert conversation.status_code == 200
    assert captured["max_tool_rounds"] == 10
    assert rejected.status_code == 422
    assert cleared.json()["source"] == "environment"


def test_mcp_tool_round_environment_limit_is_validated(monkeypatch):
    monkeypatch.setenv("HELIOS_MCP_MAX_TOOL_ROUNDS", "12")
    assert environment_max_tool_rounds() == 12

    for invalid in ("none", "0", "21"):
        monkeypatch.setenv("HELIOS_MCP_MAX_TOOL_ROUNDS", invalid)
        with pytest.raises(ValueError, match="HELIOS_MCP_MAX_TOOL_ROUNDS"):
            environment_max_tool_rounds()


def test_impala_skips_delegation_to_the_connected_user(monkeypatch):
    captured = {}

    def connect(**kwargs):
        captured.update(kwargs)
        return FakeConnection("cburns")

    monkeypatch.setattr(impala_dbapi, "connect", connect)
    engine = ImpalaEngine(
        ImpalaConfig(
            "warehouse.example", 443, "cburns", "secret",
            http_path="cliservice", proxy_delegation=True,
        )
    )
    result = engine.query("SELECT 7", delegated_user="CBurns")

    assert result.rows == [(7,)]
    assert "doAs" not in captured["http_path"]
