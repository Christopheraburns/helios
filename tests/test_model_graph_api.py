import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.console.api import ResourceStore
from apps.console.main import app
from helios_core import authz, runs as runstore
from helios_core.artifacts import ArtifactStore
from helios_core.domain import DataSourceReference, Model, Organization
from helios_core.graph import (
    ArtifactGraphRepository,
    GraphEdge,
    GraphNode,
    ModelGraph,
)


ORGANIZATION = Organization("acme", "Acme")
MODEL = Model(
    "customer360",
    ORGANIZATION.id,
    "Customer 360",
    (DataSourceReference("warehouse"),),
)


class Graphs:
    def graph_for_model(self, model):
        nodes = (
            GraphNode(
                "domain:customer",
                "domain",
                "Customer domain",
                "acme",
                model.id,
            ),
            GraphNode(
                "dataset:customers",
                "dataset",
                "Customers",
                "acme",
                model.id,
                metadata={"physical_name": "crm.customers"},
            ),
            GraphNode(
                "metric:lifetime-value",
                "metric",
                "Lifetime value",
                "acme",
                model.id,
                confidence=0.9,
                evidence="Derived from accepted semantic definitions",
            ),
            GraphNode(
                "metric:draft-score",
                "metric",
                "Draft customer score",
                "acme",
                model.id,
                status="proposed",
                confidence=0.6,
            ),
            GraphNode(
                "dataset:restricted-finance",
                "dataset",
                "Restricted finance records",
                "other",
                "finance",
            ),
        )
        edges = (
            GraphEdge(
                "edge:customers:value",
                "semantic_relationship",
                "dataset:customers",
                "metric:lifetime-value",
                "acme",
                model.id,
            ),
            GraphEdge(
                "edge:value:draft",
                "inferred_relationship",
                "metric:lifetime-value",
                "metric:draft-score",
                "acme",
                model.id,
                status="proposed",
                evidence="Draft inference",
            ),
            GraphEdge(
                "edge:value:restricted",
                "semantic_relationship",
                "metric:lifetime-value",
                "dataset:restricted-finance",
                "acme",
                model.id,
                metadata={"hidden_label": "Restricted finance records"},
            ),
        )
        return ModelGraph(model.id, model.organization_id, nodes, edges)


def grant(subject, role):
    return authz.Grant(
        f"cloudera-workbench:{subject}",
        role,
        authz.Resource("model", MODEL.id, ORGANIZATION.id),
    )


@pytest.fixture
def client():
    app.state.resource_store = ResourceStore((ORGANIZATION,), (MODEL,))
    app.state.authorization_policy = authz.Policy(
        [
            grant("owner", authz.Role.MODEL_OWNER),
            grant("viewer", authz.Role.MODEL_VIEWER),
        ]
    )
    app.state.graph_repository = Graphs()
    with TestClient(app) as test_client:
        yield test_client


def get_graph(client, subject):
    return client.get(
        f"/api/v1/models/{MODEL.id}/graph",
        headers={"x-forwarded-user": subject},
    )


def test_model_owner_sees_published_and_proposed_graph_content(client):
    response = get_graph(client, "owner")

    assert response.status_code == 200
    body = response.json()
    assert {node["id"] for node in body["nodes"]} == {
        "domain:customer",
        "dataset:customers",
        "metric:lifetime-value",
        "metric:draft-score",
    }
    assert {edge["id"] for edge in body["edges"]} == {
        "edge:customers:value",
        "edge:value:draft",
    }
    assert "model.edit" in next(
        node
        for node in body["nodes"]
        if node["id"] == "metric:draft-score"
    )["permitted_actions"]


def test_model_viewer_sees_only_authorized_published_content(client):
    response = get_graph(client, "viewer")

    assert response.status_code == 200
    body = response.json()
    assert {node["id"] for node in body["nodes"]} == {
        "domain:customer",
        "dataset:customers",
        "metric:lifetime-value",
    }
    assert {edge["id"] for edge in body["edges"]} == {
        "edge:customers:value"
    }
    assert body["summary"]["node_count"] == 3
    assert body["summary"]["edge_count"] == 1


def test_unauthorized_principal_cannot_retrieve_graph(client):
    response = get_graph(client, "outsider")

    assert response.status_code == 403


def test_other_organization_resources_never_appear(client):
    body = get_graph(client, "owner").json()
    serialized = json.dumps(body)

    assert "dataset:restricted-finance" not in serialized
    assert "Restricted finance records" not in serialized
    assert body["organization_id"] == "acme"


def test_hidden_nodes_cannot_be_inferred_from_dangling_edges(client):
    body = get_graph(client, "viewer").json()
    node_ids = {node["id"] for node in body["nodes"]}

    assert all(
        edge["source"] in node_ids and edge["target"] in node_ids
        for edge in body["edges"]
    )
    serialized = json.dumps(body)
    assert "metric:draft-score" not in serialized
    assert "edge:value:draft" not in serialized
    assert "dataset:restricted-finance" not in serialized


def test_navigation_graph_returns_bounded_authorized_neighbors(client):
    response = client.get(
        f"/api/v1/models/{MODEL.id}/graph",
        params={
            "navigation": "true",
            "focus_node_id": "dataset:customers",
            "depth": 1,
            "limit": 2,
        },
        headers={"x-forwarded-user": "viewer"},
    )

    assert response.status_code == 200
    body = response.json()
    assert {node["id"] for node in body["nodes"]} == {
        "dataset:customers",
        "metric:lifetime-value",
    }
    assert body["navigation"]["focus_node_id"] == "dataset:customers"
    assert body["navigation"]["authorized_node_count"] == 3
    assert body["navigation"]["returned_node_count"] == 2


def test_navigation_search_is_authorized_before_matching(client):
    visible = client.get(
        f"/api/v1/models/{MODEL.id}/graph",
        params={"navigation": "true", "query": "lifetime"},
        headers={"x-forwarded-user": "viewer"},
    )
    hidden = client.get(
        f"/api/v1/models/{MODEL.id}/graph",
        params={"navigation": "true", "query": "draft"},
        headers={"x-forwarded-user": "viewer"},
    )

    assert [node["id"] for node in visible.json()["nodes"]] == [
        "metric:lifetime-value"
    ]
    assert hidden.json()["nodes"] == []


def test_lenses_project_the_same_authorized_model_graph(client):
    physical = client.get(
        f"/api/v1/models/{MODEL.id}/graph",
        params={
            "navigation": "true",
            "lens": "physical",
            "focus_node_id": "dataset:customers",
        },
        headers={"x-forwarded-user": "viewer"},
    )
    semantic = client.get(
        f"/api/v1/models/{MODEL.id}/graph",
        params={
            "navigation": "true",
            "lens": "semantic",
            "focus_node_id": "dataset:customers",
        },
        headers={"x-forwarded-user": "viewer"},
    )

    assert physical.status_code == 200
    assert {node["id"] for node in physical.json()["nodes"]} == {
        "dataset:customers"
    }
    assert {node["id"] for node in semantic.json()["nodes"]} == {
        "dataset:customers",
        "metric:lifetime-value",
    }


@pytest.fixture
def review_client(tmp_path, monkeypatch):
    run_id = "review-run"
    model = Model(
        "review-model",
        ORGANIZATION.id,
        "Review model",
        (DataSourceReference("warehouse"),),
        discovery_run_ids=(run_id,),
    )
    run_directory = tmp_path / "runs" / run_id
    run_directory.mkdir(parents=True)
    proposal = {
        "datasets": [
            {
                "table": "sales.orders",
                "kind": "fact",
                "confidence": 0.88,
                "source": "catalog",
                "fields": [
                    {
                        "column": "customer_id",
                        "type": "bigint",
                        "role": "dimension_key",
                        "confidence": 0.81,
                    }
                ],
            }
        ],
        "relationships": [],
        "metrics": [],
        "glossary_terms": [],
    }
    (run_directory / "propose.json").write_text(json.dumps(proposal))
    monkeypatch.setattr(runstore, "ROOT", str(tmp_path))
    monkeypatch.setattr(runstore, "RUNS_DIR", str(tmp_path / "runs"))
    app.state.resource_store = ResourceStore((ORGANIZATION,), (model,))
    app.state.authorization_policy = authz.Policy(
        [
            authz.Grant(
                "cloudera-workbench:owner",
                authz.Role.MODEL_OWNER,
                authz.Resource("model", model.id, ORGANIZATION.id),
            ),
            authz.Grant(
                "cloudera-workbench:viewer",
                authz.Role.MODEL_VIEWER,
                authz.Resource("model", model.id, ORGANIZATION.id),
            ),
            authz.Grant(
                "cloudera-workbench:editor",
                authz.Role.MODEL_EDITOR,
                authz.Resource("model", model.id, ORGANIZATION.id),
            ),
        ]
    )
    app.state.graph_repository = ArtifactGraphRepository(ArtifactStore(tmp_path))
    with TestClient(app) as test_client:
        yield test_client, model, run_id, run_directory


def test_review_graph_requires_edit_permission(review_client):
    client, model, run_id, _ = review_client

    owner = client.get(
        f"/api/v1/models/{model.id}/graph",
        params={"review_run_id": run_id},
        headers={"x-forwarded-user": "owner"},
    )
    viewer = client.get(
        f"/api/v1/models/{model.id}/graph",
        params={"review_run_id": run_id},
        headers={"x-forwarded-user": "viewer"},
    )

    assert owner.status_code == 200
    dataset = next(
        node
        for node in owner.json()["nodes"]
        if node["id"] == "dataset:sales.orders"
    )
    assert dataset["status"] == "needs_review"
    assert "model.edit" in dataset["permitted_actions"]
    assert viewer.status_code == 403


def test_review_decision_persists_audit_and_reconciles_graph(review_client):
    client, model, run_id, run_directory = review_client
    response = client.post(
        f"/api/v1/models/{model.id}/reviews/{run_id}/decisions",
        headers={"x-forwarded-user": "owner"},
        json={
            "section": "datasets",
            "element_id": "sales.orders",
            "decision": "reject",
            "note": "Not part of this model",
        },
    )

    assert response.status_code == 200
    decision = response.json()
    assert decision["entry"] == {
        "decision": "reject",
        "note": "Not part of this model",
    }
    assert decision["reviewed_by"] == "cloudera-workbench:owner"
    assert decision["reviewed_at"]
    persisted = json.loads((run_directory / "review.json").read_text())
    assert persisted["datasets"]["sales.orders"] == decision["entry"]

    graph = client.get(
        f"/api/v1/models/{model.id}/graph",
        params={"review_run_id": run_id},
        headers={"x-forwarded-user": "owner"},
    ).json()
    dataset = next(
        node
        for node in graph["nodes"]
        if node["id"] == "dataset:sales.orders"
    )
    assert dataset["status"] == "rejected"

    detail = client.get(
        f"/api/v1/models/{model.id}/graph/detail",
        params={
            "element_id": "dataset:sales.orders",
            "review_run_id": run_id,
        },
        headers={"x-forwarded-user": "owner"},
    )
    assert detail.status_code == 200
    assert detail.json()["details"]["review_note"] == "Not part of this model"
    assert detail.json()["details"]["reviewed_by"] == "cloudera-workbench:owner"


def test_review_decision_rejects_unauthorized_or_unknown_elements(review_client):
    client, model, run_id, _ = review_client
    endpoint = f"/api/v1/models/{model.id}/reviews/{run_id}/decisions"
    body = {
        "section": "datasets",
        "element_id": "missing.dataset",
        "decision": "accept",
    }

    assert client.post(
        endpoint,
        headers={"x-forwarded-user": "viewer"},
        json=body,
    ).status_code == 403
    assert client.post(
        endpoint,
        headers={"x-forwarded-user": "owner"},
        json=body,
    ).status_code == 404


def test_review_summary_is_authorized_and_reports_audit(review_client):
    client, model, run_id, _ = review_client
    endpoint = f"/api/v1/models/{model.id}/reviews/{run_id}"

    assert client.get(
        endpoint, headers={"x-forwarded-user": "viewer"}
    ).status_code == 403
    summary = client.get(
        endpoint, headers={"x-forwarded-user": "owner"}
    )

    assert summary.status_code == 200
    body = summary.json()
    assert body["sections"]["datasets"] == {
        "accept": 0,
        "reject": 0,
        "edit": 0,
        "pending": 1,
        "total": 1,
    }
    assert body["reviewed_at"] is None
    assert body["reviewed_by"] is None
    assert body["publish_ready"] is False
    assert body["validation_errors"]
    assert "reset" in body["available_actions"]
    assert "publish" in body["available_actions"]

    assert client.get(
        f"/api/v1/models/{model.id}/reviews/another-run",
        headers={"x-forwarded-user": "owner"},
    ).status_code == 404


def test_review_cascade_bulk_and_reset_persist_audit(review_client):
    client, model, run_id, _ = review_client
    base = f"/api/v1/models/{model.id}/reviews/{run_id}"
    headers = {"x-forwarded-user": "editor"}

    cascade = client.post(
        f"{base}/decisions/dataset",
        headers=headers,
        json={"table": "sales.orders", "decision": "reject"},
    )
    assert cascade.status_code == 200
    assert cascade.json()["changed"] == 2
    assert cascade.json()["summary"]["sections"]["datasets"]["reject"] == 1
    assert cascade.json()["summary"]["sections"]["fields"]["reject"] == 1

    reset = client.post(f"{base}/reset", headers=headers, json={})
    assert reset.status_code == 200
    assert reset.json()["summary"]["sections"]["datasets"]["pending"] == 1
    assert reset.json()["summary"]["reviewed_by"] == "cloudera-workbench:editor"

    bulk = client.post(
        f"{base}/decisions/bulk",
        headers=headers,
        json={"min_confidence": 0.85},
    )
    assert bulk.status_code == 200
    assert bulk.json()["changed"] == 1
    assert bulk.json()["summary"]["sections"]["datasets"]["accept"] == 1
    assert bulk.json()["summary"]["sections"]["fields"]["pending"] == 1
    assert client.post(
        f"{base}/publish", headers=headers
    ).status_code == 403


def test_publish_review_writes_artifacts_and_reconciles_graph(review_client):
    client, model, run_id, _ = review_client
    base = f"/api/v1/models/{model.id}/reviews/{run_id}"
    headers = {"x-forwarded-user": "owner"}
    assert client.post(
        f"{base}/decisions/dataset",
        headers=headers,
        json={"table": "sales.orders", "decision": "accept"},
    ).status_code == 200

    published = client.post(f"{base}/publish", headers=headers)

    assert published.status_code == 200
    manifest = published.json()["manifest"]
    assert manifest["model_id"] == model.id
    assert manifest["run_id"] == run_id
    assert manifest["datasets"] == 1
    published_dir = Path(runstore.ROOT) / "models" / model.id / "published"
    assert (published_dir / "semantic.ossie.yaml").exists()
    assert (published_dir / "semantic.ossie.json").exists()
    assert json.loads((published_dir / "manifest.json").read_text())[
        "published_at"
    ]

    graph = client.get(
        f"/api/v1/models/{model.id}/graph",
        headers=headers,
    )
    assert graph.status_code == 200
    assert "dataset:orders" in {node["id"] for node in graph.json()["nodes"]}


def test_publish_validation_failure_does_not_write_artifacts(review_client):
    client, model, run_id, run_directory = review_client
    proposal_path = run_directory / "propose.json"
    proposal = json.loads(proposal_path.read_text())
    proposal["relationships"].append(
        {
            "from": "sales.orders",
            "from_column": "customer_id",
            "to": "missing.table",
            "to_column": "id",
            "accepted": True,
            "confidence": 0.9,
        }
    )
    proposal_path.write_text(json.dumps(proposal))
    base = f"/api/v1/models/{model.id}/reviews/{run_id}"
    headers = {"x-forwarded-user": "owner"}
    assert client.post(
        f"{base}/decisions/dataset",
        headers=headers,
        json={"table": "sales.orders", "decision": "accept"},
    ).status_code == 200
    relationship_id = "sales.orders.customer_id->missing.table.id"
    assert client.post(
        f"{base}/decisions",
        headers=headers,
        json={
            "section": "relationships",
            "element_id": relationship_id,
            "decision": "accept",
        },
    ).status_code == 200

    response = client.post(f"{base}/publish", headers=headers)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "publication_validation_failed"
    assert response.json()["detail"]["errors"]
    assert not (
        Path(runstore.ROOT)
        / "models"
        / model.id
        / "published"
        / "manifest.json"
    ).exists()
