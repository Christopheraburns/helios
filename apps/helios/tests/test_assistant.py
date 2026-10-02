import json

import pytest
from fastapi.testclient import TestClient

from apps.helios.console import ontology
from apps.helios.console.assistant import (
    AssistantService,
    build_system_prompt,
    resolve_route,
    search_docs,
)
from apps.helios.console.journeys import JOURNEYS, evaluate
from apps.helios.console.main import app
from helios_core.graph import ArtifactGraphRepository
from helios_core.llm import ToolCall, ToolTurn

PROVIDER_ENV = (
    "LLM_PROVIDER",
    "ANTHROPIC_API_KEY",
    "MISTRAL_API_KEY",
    "INFERENCE_BASE_URL",
    "HELIOS_MCP_URL",
    "HELIOS_MCP_TOKEN",
    "HELIOS_MCP_DELEGATION_SECRET",
)


def empty_state() -> dict:
    return {
        "organization": {"id": "acme", "name": "Acme"},
        "selected_model_id": None,
        "llm_provider": {"configured": False, "provider": None, "model": None},
        "mcp": {"configured": False},
        "ontology": {"version_count": 0, "active_version": None},
        "data_sources": [],
        "crawl_runs": {"total": 0, "last": None},
        "models": [],
    }


def full_state() -> dict:
    return {
        "organization": {"id": "acme", "name": "Acme"},
        "selected_model_id": "customer",
        "llm_provider": {"configured": True, "provider": "anthropic", "model": "claude"},
        "mcp": {"configured": True},
        "ontology": {"version_count": 2, "active_version": "0.2.0"},
        "data_sources": [
            {
                "id": "docs",
                "name": "Docs",
                "connector": "object_store",
                "crawl_enabled": True,
                "last_crawl": {
                    "crawl_run_id": "crawl-1",
                    "status": "SUCCEEDED",
                    "started_at": "2026-01-01T00:00:00Z",
                },
            }
        ],
        "crawl_runs": {
            "total": 1,
            "last": {
                "crawl_run_id": "crawl-1",
                "status": "SUCCEEDED",
                "started_at": "2026-01-01T00:00:00Z",
                "source": "docs",
            },
        },
        "models": [
            {
                "id": "customer",
                "name": "Customer",
                "data_source_count": 1,
                "lifecycle": {
                    "publication_state": "published",
                    "discovery_status": "proposals_ready",
                    "review_status": "complete",
                    "unresolved_review_items": 0,
                    "latest_run_id": "run-1",
                },
                "summary": {
                    "dataset_count": 1,
                    "relationship_count": 0,
                    "concept_count": 0,
                    "metric_count": 1,
                },
                "has_conversations": True,
                "available_actions": ["model.read"],
            }
        ],
    }


def by_id(items):
    return {item["id"]: item for item in items}


def test_empty_state_has_nothing_done():
    journeys = by_id(evaluate(JOURNEYS, empty_state()))

    assert set(journeys) == {"semantic-model", "unstructured-crawl"}
    for journey in journeys.values():
        assert journey["progress"]["done"] == 0
        assert journey["next_step_id"] == "configure-llm"
        assert all(step["status"] in {"todo", "external"} for step in journey["steps"])
    semantic = by_id(journeys["semantic-model"]["steps"])
    assert semantic["create-model"]["status"] == "external"
    assert semantic["run-discovery"]["status"] == "external"
    assert "Workbench Job" in semantic["run-discovery"]["note"]
    assert semantic["review-proposals"]["status"] == "todo"
    assert semantic["review-proposals"]["params"] == {}
    assert semantic["review-proposals"]["note"] is None
    crawl = by_id(journeys["unstructured-crawl"]["steps"])
    assert crawl["run-crawl"]["status"] == "external"


def test_full_state_is_all_done():
    for journey in evaluate(JOURNEYS, full_state()):
        assert journey["progress"]["done"] == journey["progress"]["total"]
        assert journey["next_step_id"] is None
        assert all(step["status"] == "done" for step in journey["steps"])
        assert all(step["note"] is None for step in journey["steps"])


def test_partial_state_reports_next_step_and_review_run():
    state = full_state()
    state["models"][0]["lifecycle"]["review_status"] = "pending"
    state["models"][0]["lifecycle"]["publication_state"] = "proposed"
    state["models"][0]["has_conversations"] = False
    state["ontology"]["active_version"] = None

    journeys = by_id(evaluate(JOURNEYS, state))
    semantic = journeys["semantic-model"]
    assert semantic["progress"] == {"done": 4, "total": 7}
    assert semantic["next_step_id"] == "review-proposals"
    review = by_id(semantic["steps"])["review-proposals"]
    assert review["route"] == "/canvas"
    assert review["params"] == {"review_run_id": "run-1"}
    crawl = journeys["unstructured-crawl"]
    assert crawl["next_step_id"] == "activate-ontology"
    assert crawl["progress"] == {"done": 5, "total": 6}


def test_steps_fall_back_to_first_model_when_none_selected():
    state = full_state()
    state["selected_model_id"] = None
    semantic = by_id(evaluate(JOURNEYS, state))["semantic-model"]
    assert semantic["progress"]["done"] == 7


@pytest.fixture
def assistant_client(persistent_auth_stack, monkeypatch, tmp_path):
    for name in PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("HELIOS_DEV", raising=False)
    monkeypatch.setenv("HELIOS_GRAPH_STORE_DIR", str(tmp_path / "graph-store"))
    monkeypatch.setattr(ontology, "index_store", lambda: None)
    previous = dict(app.state._state)
    app.state.metadata_repository = persistent_auth_stack.repository
    app.state.graph_repository = ArtifactGraphRepository(
        persistent_auth_stack.artifacts
    )
    for key in ("resource_store", "authorization_policy", "assistant_service"):
        app.state._state.pop(key, None)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.state._state.clear()
        app.state._state.update(previous)


def headers(subject):
    return {"x-forwarded-user": subject}


def test_workspace_state_requires_principal(assistant_client):
    response = assistant_client.get(
        "/api/v1/assistant/workspace-state", params={"organization": "acme"}
    )
    assert response.status_code == 401


def test_workspace_state_denies_outsider(assistant_client):
    response = assistant_client.get(
        "/api/v1/assistant/workspace-state",
        params={"organization": "acme"},
        headers=headers("outsider"),
    )
    assert response.status_code == 403


def test_workspace_state_allows_model_only_principal(assistant_client):
    response = assistant_client.get(
        "/api/v1/assistant/workspace-state",
        params={"organization": "acme"},
        headers=headers("owner"),
    )
    assert response.status_code == 200, response.text
    state = response.json()
    assert [model["id"] for model in state["models"]] == ["customer360"]


def test_workspace_state_shape_for_organization_admin(assistant_client):
    response = assistant_client.get(
        "/api/v1/assistant/workspace-state",
        params={"organization": "acme", "model": "customer360"},
        headers=headers("admin"),
    )
    assert response.status_code == 200, response.text
    state = response.json()
    assert set(state) == {
        "organization",
        "selected_model_id",
        "llm_provider",
        "mcp",
        "ontology",
        "data_sources",
        "crawl_runs",
        "models",
        "journeys",
    }
    assert state["organization"] == {"id": "acme", "name": "Acme"}
    assert state["selected_model_id"] == "customer360"
    assert state["llm_provider"] == {"configured": False, "provider": None, "model": None}
    assert state["mcp"] == {"configured": False}
    assert state["ontology"] == {"version_count": 0, "active_version": None}
    assert state["crawl_runs"] == {"total": 0, "last": None}
    assert [source["id"] for source in state["data_sources"]] == ["shared-warehouse"]
    assert state["data_sources"][0]["crawl_enabled"] is False
    assert state["data_sources"][0]["last_crawl"] is None
    models = by_id(state["models"])
    assert set(models) == {"customer360", "finance"}
    customer = models["customer360"]
    assert customer["lifecycle"]["publication_state"] == "published"
    assert set(customer["lifecycle"]) == {
        "publication_state",
        "discovery_status",
        "review_status",
        "unresolved_review_items",
        "latest_run_id",
    }
    assert customer["summary"]["dataset_count"] == 1
    assert customer["has_conversations"] is False
    assert "model.read" in customer["available_actions"]
    assert customer["data_source_count"] == 1
    assert len(state["journeys"]) == 2
    semantic = by_id(state["journeys"])["semantic-model"]
    steps = by_id(semantic["steps"])
    assert steps["add-data-source"]["status"] == "done"
    assert steps["create-model"]["status"] == "done"
    assert steps["publish-model"]["status"] == "done"
    assert steps["configure-llm"]["status"] == "todo"


def test_workspace_state_rejects_unknown_model(assistant_client):
    response = assistant_client.get(
        "/api/v1/assistant/workspace-state",
        params={"organization": "acme", "model": "nope"},
        headers=headers("admin"),
    )
    assert response.status_code == 404


class NavigatingLLM:
    provider = "fake"
    model = "fake-model"

    def __init__(self, route="/data-sources"):
        self.route = route
        self.turns = 0
        self.messages = []
        self.system = ""

    def tool_turn(self, system, messages, tools):
        self.turns += 1
        self.system = system
        self.messages = list(messages)
        assert {tool["name"] for tool in tools} == {
            "get_workspace_state",
            "search_docs",
            "navigate",
        }
        if self.turns == 1:
            return ToolTurn(
                "",
                (ToolCall("call-1", "navigate", {"route": self.route, "label": "Go"}),),
                "tool_use",
            )
        return ToolTurn("Add a data source first.", (), "stop")


def test_turn_returns_navigation_action(assistant_client):
    llm = NavigatingLLM()
    app.state.assistant_service = AssistantService(
        llm, trace_repository=app.state.metadata_repository
    )
    response = assistant_client.post(
        "/api/v1/assistant/turn",
        headers=headers("admin"),
        json={
            "organization": "acme",
            "model": "customer360",
            "message": "What should I do next?",
            "history": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
            "location": {"pathname": "/canvas", "search": "?lens=semantic"},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"] == "Add a data source first."
    assert len(body["actions"]) == 1
    action = body["actions"][0]
    assert action["type"] == "navigate"
    assert action["route"] == "/data-sources"
    assert action["params"]["organization"] == "acme"
    assert action["params"]["model"] == "customer360"
    assert action["label"] == "Go"
    assert body["tool_trace"] == [
        {"name": "navigate", "arguments": {"route": "/data-sources", "label": "Go"}, "ok": True}
    ]
    assert body["provenance"] == {"llm": {"provider": "fake", "model": "fake-model"}}
    assert body["request_id"]
    assert "trace_run_id" in body
    assert "/canvas?lens=semantic" in llm.system
    assert "semantic-model" not in llm.system or "configure-llm: todo" in llm.system
    roles = [message["role"] for message in llm.messages]
    assert roles == ["user", "assistant", "user", "assistant", "tool"]
    assert llm.messages[-1]["tool_call_id"] == "call-1"


def test_turn_rejects_unknown_route(assistant_client):
    llm = NavigatingLLM(route="/secret-admin")
    app.state.assistant_service = AssistantService(llm)
    response = assistant_client.post(
        "/api/v1/assistant/turn",
        headers=headers("admin"),
        json={"organization": "acme", "message": "Take me somewhere"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["actions"] == []
    assert body["tool_trace"][0]["ok"] is False
    tool_result = json.loads(llm.messages[-1]["content"])
    assert tool_result["error"] == "unknown_route"
    assert "/data-sources" in tool_result["routes"]


def test_turn_requires_a_message(assistant_client):
    app.state.assistant_service = AssistantService(NavigatingLLM())
    empty = assistant_client.post(
        "/api/v1/assistant/turn",
        headers=headers("admin"),
        json={"organization": "acme", "message": "   "},
    )
    assert empty.status_code == 400
    long = assistant_client.post(
        "/api/v1/assistant/turn",
        headers=headers("admin"),
        json={"organization": "acme", "message": "x" * 2001},
    )
    assert long.status_code == 422


def test_turn_denies_outsider(assistant_client):
    app.state.assistant_service = AssistantService(NavigatingLLM())
    response = assistant_client.post(
        "/api/v1/assistant/turn",
        headers=headers("outsider"),
        json={"organization": "acme", "message": "hello"},
    )
    assert response.status_code == 403


def test_turn_reports_missing_llm(assistant_client):
    response = assistant_client.post(
        "/api/v1/assistant/turn",
        headers=headers("admin"),
        json={"organization": "acme", "message": "hello"},
    )
    assert response.status_code == 503
    assert "LLM" in response.json()["detail"]


def test_search_docs_finds_review_guidance():
    results = search_docs("review proposals publish")
    assert results
    assert results[0]["slug"] in {"console-review", "walkthrough"}
    assert results[0]["route"] == f"/docs/helios/{results[0]['slug']}"
    assert len(results[0]["excerpt"]) <= 600
    assert len(results) <= 5
    assert search_docs("") == []


def test_resolve_route_accepts_catalog_and_doc_routes():
    assert resolve_route("/canvas?lens=semantic") == ("/canvas", {"lens": "semantic"})
    assert resolve_route("/models/runs/run-1") == ("/models/runs/run-1", {})
    assert resolve_route("/docs/helios/walkthrough") == ("/docs/helios/walkthrough", {})
    assert resolve_route("/docs/helios/unknown") is None
    assert resolve_route("/nope") is None


def test_system_prompt_summarizes_journeys():
    state = full_state()
    state["journeys"] = evaluate(JOURNEYS, state)
    prompt = build_system_prompt(state, "acme", "customer", None)
    assert "Helios navigation assistant" in prompt
    assert "/governance/model-provider" in prompt
    assert "ask-question: done" in prompt
