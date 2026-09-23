import json

import pytest
from fastapi.testclient import TestClient

from apps.console.api import ResourceStore
from apps.console.main import app
from helios_core import authz
from helios_core.domain import DataSourceReference, Model, Organization
from helios_core.graph import GraphEdge, GraphNode, ModelGraph


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
