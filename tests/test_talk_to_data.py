import json
from types import SimpleNamespace

import httpx2
import pytest
import impala.dbapi as impala_dbapi
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from apps.console import conversation as conversation_module
from apps.console.conversation import ConversationService, MCPClientConfig
from apps.console.main import app
from apps.mcp import server as mcp_server
from helios_core import audit, authz
from helios_core.config import ImpalaConfig
from helios_core.data_authorization import (
    DataPolicy,
    PlatformDataDecision,
)
from helios_core.delegation import (
    DelegationError,
    issue_assertion,
    verify_assertion,
)
from helios_core.engines.impala import ImpalaEngine
from helios_core.llm import LLMClient, ToolCall, ToolTurn, llm_from_env
from helios_core.metadata import ConversationVersionConflict
from helios_core.ossie import SemanticModel, build as build_ossie


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


def test_signed_delegation_round_trip_and_tamper_rejection():
    token = issue_assertion(
        SECRET,
        principal(),
        "acme",
        "customer360",
        now=100,
        request_id="request-123",
        session_id="session-456",
    )
    context = verify_assertion(SECRET, token, now=110)

    assert context.principal.id == "cloudera-workbench:owner"
    assert context.organization_id == "acme"
    assert context.model_id == "customer360"
    assert context.request_id == "request-123"
    assert context.session_id == "session-456"
    with pytest.raises(DelegationError):
        verify_assertion(SECRET, token[:-1] + "x", now=110)
    with pytest.raises(DelegationError, match="expired"):
        verify_assertion(SECRET, token, now=200)


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
                ]
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

    assert repository.schema_version() == 4
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
        missing = mcp_server.describe(
            "not-a-semantic-object",
            model="customer360",
        )
    finally:
        mcp_server._caller_context.reset(context_token)

    assert described["datasets"][0]["table"] == "warehouse.customers"
    assert searched["matches"][0]["kind"] in {"dataset", "metric", "field"}
    assert field["column"] == "customer_id"
    assert compiled["dataset"] == "warehouse.customers"
    assert "GROUP BY" in compiled["sql"]
    assert invalid["error"] == "invalid_semantic_query"
    assert invalid["retryable"] is False
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
        result = mcp_server.run_query(
            ["Customer count"], limit=5000, model="customer360"
        )
    finally:
        mcp_server._caller_context.reset(context_token)

    assert result["rows"] == [[12]]
    assert captured["delegated_user"] == "owner"
    assert captured["limit"] == 1000

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

    async def call_tool(self, name, arguments):
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
        == "The requested semantic object was not found."
    )
    assert len(result["tool_trace"]) == 2


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
