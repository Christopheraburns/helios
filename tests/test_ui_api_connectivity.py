import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.console.api import api_router
from apps.console.main import configure_cors, configured_cors_origins
from apps.ui.app import configured_api_url
from helios_core import authz
from helios_core.domain import (
    DataSource,
    DataSourceReference,
    Model,
    Organization,
)
from helios_core.metadata import PrincipalRecord, SQLiteMetadataRepository


def test_cors_configuration_requires_exact_origins():
    assert configured_cors_origins(
        "https://helios-ui.example.test, http://localhost:5173/"
    ) == [
        "https://helios-ui.example.test",
        "http://localhost:5173",
    ]

    with pytest.raises(ValueError, match="exact origins"):
        configured_cors_origins("*")


def test_ui_runtime_api_url_is_required_and_validated(monkeypatch):
    monkeypatch.setenv("HELIOS_API_URL", "https://helios-api.example.test/")
    assert configured_api_url() == "https://helios-api.example.test"

    monkeypatch.setenv("HELIOS_API_URL", "https://helios-api.example.test/path")
    with pytest.raises(SystemExit, match="HELIOS_API_URL"):
        configured_api_url()


@pytest.fixture
def connectivity_client(tmp_path):
    repository = SQLiteMetadataRepository(tmp_path / "metadata.db")
    repository.migrate()
    repository.save_organization(Organization("acme", "Acme"), "acme")
    repository.save_organization(Organization("other", "Other"), "other")
    repository.save_principal(
        PrincipalRecord(
            "cloudera-workbench:alice",
            "alice",
            "Alice",
        )
    )
    repository.save_principal(
        PrincipalRecord("cloudera-workbench:bob", "bob", "Bob")
    )
    repository.save_principal(
        PrincipalRecord("cloudera-workbench:carol", "carol", "Carol")
    )
    repository.save_data_source(
        DataSource("acme-source", "acme", "Acme Source", "impala", "acme-ref")
    )
    repository.save_data_source(
        DataSource(
            "other-source",
            "other",
            "Other Source",
            "impala",
            "other-ref",
        )
    )
    repository.save_model(
        Model(
            "customer",
            "acme",
            "Customer",
            (DataSourceReference("acme-source"),),
        ),
        "cloudera-workbench:alice",
        status="published",
    )
    repository.save_model(
        Model(
            "finance",
            "other",
            "Finance",
            (DataSourceReference("other-source"),),
        ),
        "cloudera-workbench:bob",
        status="published",
    )
    repository.add_organization_membership(
        "acme",
        "cloudera-workbench:alice",
        authz.Role.ORG_ADMIN,
    )
    repository.add_model_grant(
        "finance",
        "cloudera-workbench:carol",
        authz.Role.MODEL_VIEWER,
    )

    test_app = FastAPI()
    configure_cors(test_app, "https://helios-ui.example.test")
    test_app.state.metadata_repository = repository
    test_app.include_router(api_router)
    with TestClient(test_app) as client:
        yield client


def test_health_is_ready_without_application_identity(connectivity_client):
    response = connectivity_client.get("/api/v1/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_diagnostics_uses_cloudera_transparent_identity(connectivity_client):
    response = connectivity_client.get(
        "/api/v1/diagnostics",
        headers={"remote-user": "alice"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "principal": {
            "id": "cloudera-workbench:alice",
            "issuer": "cloudera-workbench",
            "subject": "alice",
            "display_name": "alice",
            "kind": "human",
        },
        "accessible_organization_count": 1,
    }


def test_diagnostics_distinguishes_authentication_from_zero_access(
    connectivity_client,
):
    unauthenticated = connectivity_client.get("/api/v1/diagnostics")
    no_access = connectivity_client.get(
        "/api/v1/diagnostics",
        headers={"remote-user": "bob"},
    )

    assert unauthenticated.status_code == 401
    assert no_access.status_code == 200
    assert no_access.json()["principal"]["subject"] == "bob"
    assert no_access.json()["accessible_organization_count"] == 0


def test_development_identity_requires_explicit_dev_mode(
    connectivity_client,
    monkeypatch,
):
    monkeypatch.setenv("HELIOS_DEV_USER", "alice")
    disabled = connectivity_client.get("/api/v1/diagnostics")

    monkeypatch.setenv("HELIOS_DEV", "1")
    enabled = connectivity_client.get("/api/v1/diagnostics")

    assert disabled.status_code == 401
    assert enabled.status_code == 200


def test_api_allows_only_configured_credentialed_origin(connectivity_client):
    allowed = connectivity_client.options(
        "/api/v1/diagnostics",
        headers={
            "origin": "https://helios-ui.example.test",
            "access-control-request-method": "GET",
        },
    )
    denied = connectivity_client.options(
        "/api/v1/diagnostics",
        headers={
            "origin": "https://attacker.example.test",
            "access-control-request-method": "GET",
        },
    )

    assert allowed.status_code == 200
    assert (
        allowed.headers["access-control-allow-origin"]
        == "https://helios-ui.example.test"
    )
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


def test_api_allows_review_posts_only_from_configured_origin(
    connectivity_client,
):
    allowed = connectivity_client.options(
        "/api/v1/models/customer/reviews/run-1/decisions",
        headers={
            "origin": "https://helios-ui.example.test",
            "access-control-request-method": "POST",
            "access-control-request-headers": "content-type",
        },
    )
    denied = connectivity_client.options(
        "/api/v1/models/customer/reviews/run-1/decisions",
        headers={
            "origin": "https://attacker.example.test",
            "access-control-request-method": "POST",
            "access-control-request-headers": "content-type",
        },
    )

    assert allowed.status_code == 200
    assert (
        allowed.headers["access-control-allow-origin"]
        == "https://helios-ui.example.test"
    )
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


def test_navigation_collections_return_only_accessible_resources(
    connectivity_client,
):
    alice_headers = {"remote-user": "alice"}
    organizations = connectivity_client.get(
        "/api/v1/organizations",
        headers=alice_headers,
    )
    models = connectivity_client.get(
        "/api/v1/models?organization_id=acme",
        headers=alice_headers,
    )

    assert organizations.status_code == 200
    assert [item["id"] for item in organizations.json()["organizations"]] == [
        "acme"
    ]
    assert [item["id"] for item in models.json()["models"]] == ["customer"]


def test_model_scoped_principal_can_discover_selector_context(
    connectivity_client,
):
    headers = {"remote-user": "carol"}
    organizations = connectivity_client.get(
        "/api/v1/organizations",
        headers=headers,
    )
    models = connectivity_client.get(
        "/api/v1/models?organization_id=other",
        headers=headers,
    )

    assert organizations.status_code == 200
    assert organizations.json()["organizations"] == [
        {
            "id": "other",
            "name": "Other",
            "available_actions": [],
        }
    ]
    assert models.status_code == 200
    assert [item["id"] for item in models.json()["models"]] == ["finance"]
