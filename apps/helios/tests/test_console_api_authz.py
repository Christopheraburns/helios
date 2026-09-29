import pytest
from fastapi.testclient import TestClient

from apps.console.main import app
from apps.console.api import ResourceStore
from helios_core import authz
from helios_core.domain import DataSourceReference, Model, Organization


ORG_ACME = Organization("acme", "Acme")
ORG_OTHER = Organization("other", "Other")
CUSTOMER = Model(
    id="customer360",
    organization_id="acme",
    name="Customer 360",
    data_sources=(DataSourceReference("warehouse", ("crm.customers",)),),
    version_ids=("v1",),
    discovery_run_ids=("run-1",),
    glossary_id="customer-glossary",
    semantic_model_id="customer-semantic",
    ontology_id="customer-ontology",
)
FINANCE = Model(
    id="finance",
    organization_id="other",
    name="Finance",
    data_sources=(DataSourceReference("finance-warehouse"),),
)


def principal_id(subject: str) -> str:
    return f"cloudera-workbench:{subject}"


class AtlasStub:
    def get_glossary(self, guid):
        return {"guid": guid, "name": "Customer glossary", "terms": []}

    def list_terms(self, glossary_guid, limit=1000, offset=0):
        return []


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("CDSW_USER", raising=False)
    previous = dict(app.state._state)
    app.state.resource_store = ResourceStore(
        (ORG_ACME, ORG_OTHER), (CUSTOMER, FINANCE)
    )
    app.state.authorization_policy = authz.Policy(
        [
            authz.Grant(
                principal_id("alice"),
                authz.Role.MODEL_EDITOR,
                authz.Resource("model", CUSTOMER.id, ORG_ACME.id),
            ),
            authz.Grant(
                principal_id("chris"),
                authz.Role.ORG_ADMIN,
                authz.Resource("organization", ORG_ACME.id, ORG_ACME.id),
            ),
            authz.Grant(
                principal_id("mallory"),
                authz.Role.MODEL_OWNER,
                authz.Resource("model", FINANCE.id, ORG_OTHER.id),
            ),
        ]
    )
    app.state.atlas_client = AtlasStub()
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.state._state.clear()
        app.state._state.update(previous)


def test_authorized_model_response_includes_available_actions(client):
    response = client.get(
        "/api/v1/models/customer360",
        headers={"x-forwarded-user": "alice"},
    )

    assert response.status_code == 200
    assert response.json()["id"] == "customer360"
    assert "model.edit" in response.json()["available_actions"]
    assert "model.delete" not in response.json()["available_actions"]


def test_unauthorized_request_is_rejected(client):
    response = client.get(
        "/api/v1/models/customer360",
        headers={"x-forwarded-user": "bob"},
    )

    assert response.status_code == 403


def test_model_grant_does_not_cross_organization_boundary(client):
    response = client.get(
        "/api/v1/models/finance",
        headers={"x-forwarded-user": "alice"},
    )

    assert response.status_code == 403


def test_org_admin_inherits_access_to_owned_models_only(client):
    owned = client.get(
        "/api/v1/models/customer360",
        headers={"x-forwarded-user": "chris"},
    )
    other = client.get(
        "/api/v1/models/finance",
        headers={"x-forwarded-user": "chris"},
    )

    assert owned.status_code == 200
    assert "model.delete" in owned.json()["available_actions"]
    assert other.status_code == 403


def test_nested_model_resource_uses_same_policy_dependency(client):
    allowed = client.get(
        "/api/v1/models/customer360/glossary",
        headers={"x-forwarded-user": "alice"},
    )
    denied = client.get(
        "/api/v1/models/finance/glossary",
        headers={"x-forwarded-user": "alice"},
    )

    assert allowed.status_code == 200
    assert allowed.json()["glossary_id"] == "customer-glossary"
    assert denied.status_code == 403


def test_organization_endpoint_requires_org_scoped_permission(client):
    admin = client.get(
        "/api/v1/organizations/acme",
        headers={"x-forwarded-user": "chris"},
    )
    model_editor = client.get(
        "/api/v1/organizations/acme",
        headers={"x-forwarded-user": "alice"},
    )

    assert admin.status_code == 200
    assert model_editor.status_code == 403


def test_api_requires_an_authenticated_principal(client):
    response = client.get("/api/v1/models/customer360")

    assert response.status_code == 401
