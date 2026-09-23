import pytest

from helios_core.authorization import (
    HeliosResource,
    ResourceAuthorizationDecision,
    ResourceAuthorizationError,
    require_resource_access,
)
from helios_core.data_authorization import (
    DataAuthorizationDecision,
    DataResource,
)
from helios_core.identity import Principal, PrincipalKind


@pytest.fixture
def human() -> Principal:
    return Principal(
        issuer="cloudera-workbench",
        subject="user-123",
        kind=PrincipalKind.HUMAN,
        display_name="Ada",
        claims={"email": "ada@example.com"},
    )


class AllowModelReader:
    def authorize(self, principal, action, resource):
        if (
            principal.subject == "user-123"
            and action == "read"
            and resource.resource_type == "model"
        ):
            return ResourceAuthorizationDecision.allow()
        return ResourceAuthorizationDecision.deny("model reader access required")


class DenyWarehouseReader:
    def __init__(self):
        self.calls = 0

    def authorize_data(self, principal, action, resource):
        self.calls += 1
        return DataAuthorizationDecision.deny("lakehouse policy denied access")


def test_principal_represents_human_service_and_agent_identities():
    principals = [
        Principal("workbench", "user-1", PrincipalKind.HUMAN),
        Principal("helios", "discovery-job", PrincipalKind.SERVICE),
        Principal("mcp", "analytics-agent", PrincipalKind.AGENT),
    ]

    assert [principal.id for principal in principals] == [
        "workbench:user-1",
        "helios:discovery-job",
        "mcp:analytics-agent",
    ]


def test_principal_claims_are_context_not_mutable_permissions(human):
    with pytest.raises(TypeError):
        human.claims["role"] = "admin"


def test_resource_authorization_is_transport_independent(human):
    resource = HeliosResource("model", "sales", organization_id="acme")

    require_resource_access(AllowModelReader(), human, "read", resource)
    with pytest.raises(ResourceAuthorizationError, match="model reader"):
        require_resource_access(AllowModelReader(), human, "delete", resource)


def test_helios_resource_grant_does_not_imply_data_access(human):
    model_resource = HeliosResource("model", "sales", organization_id="acme")
    data_resource = DataResource("warehouse", "sales.orders")
    data_authorizer = DenyWarehouseReader()

    require_resource_access(AllowModelReader(), human, "read", model_resource)
    assert data_authorizer.calls == 0

    decision = data_authorizer.authorize_data(human, "select", data_resource)
    assert decision == DataAuthorizationDecision.deny(
        "lakehouse policy denied access"
    )
    assert data_authorizer.calls == 1


def test_principal_and_authorization_modules_do_not_import_fastapi():
    import helios_core.authorization as resource_module
    import helios_core.data_authorization as data_module
    import helios_core.identity as identity_module

    for module in (identity_module, resource_module, data_module):
        assert "fastapi" not in module.__dict__
