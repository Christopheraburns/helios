import pytest
from fastapi.testclient import TestClient

from apps.console.main import app
from helios_core.graph import ArtifactGraphRepository


def headers(subject):
    return {"x-forwarded-user": subject}


@pytest.fixture
def persistent_client(persistent_auth_stack):
    previous = dict(app.state._state)
    app.state._state.pop("resource_store", None)
    app.state._state.pop("authorization_policy", None)
    app.state._state.pop("graph_repository", None)
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


def test_real_dependencies_return_authentication_and_lookup_statuses(
    persistent_client,
):
    missing_identity = persistent_client.get(
        "/api/v1/models/customer360"
    )
    missing_resource = persistent_client.get(
        "/api/v1/models/does-not-exist",
        headers=headers("owner"),
    )
    denied = persistent_client.get(
        "/api/v1/models/customer360",
        headers=headers("outsider"),
    )
    allowed = persistent_client.get(
        "/api/v1/models/customer360",
        headers=headers("owner"),
    )

    assert missing_identity.status_code == 401
    assert missing_resource.status_code == 404
    assert denied.status_code == 403
    assert allowed.status_code == 200


def test_persisted_roles_drive_available_actions(persistent_client):
    owner = persistent_client.get(
        "/api/v1/models/customer360",
        headers=headers("owner"),
    ).json()
    viewer = persistent_client.get(
        "/api/v1/models/customer360",
        headers=headers("viewer"),
    ).json()
    editor = persistent_client.get(
        "/api/v1/models/finance",
        headers=headers("editor"),
    ).json()

    assert {"model.edit", "model.delete", "model.publish"} <= set(
        owner["available_actions"]
    )
    assert "model.read" in viewer["available_actions"]
    assert "model.edit" not in viewer["available_actions"]
    assert "model.edit" in editor["available_actions"]
    assert "model.delete" not in editor["available_actions"]


def test_org_admin_inherits_only_within_persisted_organization(
    persistent_client,
):
    organization = persistent_client.get(
        "/api/v1/organizations/acme",
        headers=headers("admin"),
    )
    models = persistent_client.get(
        "/api/v1/organizations/acme/models",
        headers=headers("admin"),
    )
    foreign_model = persistent_client.get(
        "/api/v1/models/other-model",
        headers=headers("admin"),
    )

    assert organization.status_code == 200
    assert models.status_code == 200
    assert {model["id"] for model in models.json()["models"]} == {
        "customer360",
        "finance",
    }
    assert foreign_model.status_code == 403


@pytest.mark.parametrize(
    "suffix, expected_key, expected_value",
    [
        ("glossary", "glossary_id", "customer-glossary"),
        ("semantic", "semantic_model_id", "customer-semantic"),
        ("ontology", "ontology_id", "customer-ontology"),
    ],
)
def test_nested_resources_use_persisted_model_scope(
    persistent_client,
    suffix,
    expected_key,
    expected_value,
):
    allowed = persistent_client.get(
        f"/api/v1/models/customer360/{suffix}",
        headers=headers("viewer"),
    )
    denied = persistent_client.get(
        f"/api/v1/models/finance/{suffix}",
        headers=headers("viewer"),
    )

    assert allowed.status_code == 200
    assert allowed.json()[expected_key] == expected_value
    assert denied.status_code == 403


def test_runs_and_versions_endpoints_are_authorized_by_persisted_grants(
    persistent_client,
):
    runs = persistent_client.get(
        "/api/v1/models/customer360/runs",
        headers=headers("viewer"),
    )
    versions = persistent_client.get(
        "/api/v1/models/customer360/versions",
        headers=headers("viewer"),
    )
    denied_runs = persistent_client.get(
        "/api/v1/models/finance/runs",
        headers=headers("viewer"),
    )

    assert runs.status_code == 200
    assert versions.status_code == 200
    assert denied_runs.status_code == 403
