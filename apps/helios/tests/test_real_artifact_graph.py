import json

import pytest
from fastapi.testclient import TestClient

from apps.console.main import app
from helios_core.graph import ArtifactGraphRepository


@pytest.fixture
def artifact_graph_client(persistent_auth_stack):
    previous = dict(app.state._state)
    app.state._state.pop("resource_store", None)
    app.state._state.pop("authorization_policy", None)
    app.state.metadata_repository = persistent_auth_stack.repository
    app.state.graph_repository = ArtifactGraphRepository(
        persistent_auth_stack.artifacts
    )
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.state._state.clear()
        app.state._state.update(previous)


def graph(client, model_id, subject):
    return client.get(
        f"/api/v1/models/{model_id}/graph",
        headers={"x-forwarded-user": subject},
    )


def test_models_sharing_data_source_return_independent_real_artifacts(
    artifact_graph_client,
):
    customer = graph(
        artifact_graph_client, "customer360", "owner"
    )
    finance = graph(artifact_graph_client, "finance", "editor")

    assert customer.status_code == 200
    assert finance.status_code == 200
    customer_json = json.dumps(customer.json())
    finance_json = json.dumps(finance.json())

    assert "dataset:customers" in customer_json
    assert "metric:customer_count" in customer_json
    assert "dataset:ledger" not in customer_json
    assert "metric:account_count" not in customer_json

    assert "dataset:ledger" in finance_json
    assert "metric:account_count" in finance_json
    assert "dataset:customers" not in finance_json
    assert "metric:customer_count" not in finance_json


def test_real_artifact_graph_contract_for_model_viewer(
    artifact_graph_client,
):
    response = graph(
        artifact_graph_client, "customer360", "viewer"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["model_id"] == "customer360"
    assert body["organization_id"] == "acme"
    assert body["summary"]["node_count"] == len(body["nodes"])
    assert body["summary"]["edge_count"] == len(body["edges"])
    assert all(
        {
            "id",
            "kind",
            "label",
            "status",
            "confidence",
            "evidence",
            "metadata",
            "permitted_actions",
        }
        <= node.keys()
        for node in body["nodes"]
    )
    node_ids = {node["id"] for node in body["nodes"]}
    assert all(
        edge["source"] in node_ids and edge["target"] in node_ids
        for edge in body["edges"]
    )


def test_real_graph_never_exposes_foreign_organization_artifacts(
    artifact_graph_client,
):
    response = graph(
        artifact_graph_client, "customer360", "owner"
    )

    serialized = json.dumps(response.json())
    assert response.status_code == 200
    assert "restricted" not in serialized
    assert "other-model" not in serialized
    assert "other-warehouse" not in serialized


def test_real_graph_access_is_denied_without_model_grant(
    artifact_graph_client,
):
    response = graph(
        artifact_graph_client, "customer360", "outsider"
    )

    assert response.status_code == 403
