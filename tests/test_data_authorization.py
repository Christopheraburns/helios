import pytest

from helios_core import authz
from helios_core.config import impala_config
from helios_core.data_authorization import (
    DataAction,
    DataAuthorizationDenied,
    DataPolicy,
    DataResource,
    DataRestrictionDecision,
    PlatformDataDecision,
)


PRINCIPAL = authz.Principal(
    "workbench", "alice", authz.PrincipalKind.HUMAN
)
RESOURCE = DataResource("warehouse", "sales.orders")


def test_impala_config_uses_workload_credentials(monkeypatch):
    for name in (
        "WORKLOAD_USER",
        "WORKLOAD_PASSWORD",
        "IMPALA_USER",
        "IMPALA_PASS",
        "ATLAS_USER",
        "ATLAS_PASS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("IMPALA_HOST", "warehouse.example")
    monkeypatch.setenv("IMPALA_USER", "legacy-user")
    monkeypatch.setenv("IMPALA_PASS", "legacy-password")

    assert impala_config() is None

    monkeypatch.setenv("WORKLOAD_USER", "workload-user")
    monkeypatch.setenv("WORKLOAD_PASSWORD", "workload-password")
    configuration = impala_config()

    assert configuration is not None
    assert configuration.user == "workload-user"
    assert configuration.password == "workload-password"


class Platform:
    def __init__(self, decision):
        self.decision = decision
        self.calls = 0

    def can_access(self, principal, resource, action):
        self.calls += 1
        return self.decision


class Restriction:
    def __init__(self, decision):
        self.decision = decision

    def can_access(self, principal, resource, action):
        return self.decision


def test_default_data_policy_fails_closed():
    decision = DataPolicy().can_access(
        PRINCIPAL, RESOURCE, DataAction.QUERY_EXECUTE
    )

    assert not decision.allowed
    assert decision.denied_by == "platform"
    assert not decision.platform_enforced
    assert "unavailable" in decision.reason


def test_helios_cannot_override_platform_denial():
    platform = Platform(PlatformDataDecision.deny("Ranger denied access"))
    policy = DataPolicy(
        platform,
        restrictions=[Restriction(DataRestrictionDecision.allow())],
    )

    decision = policy.can_access(
        PRINCIPAL, RESOURCE, DataAction.QUERY_EXECUTE
    )

    assert not decision.allowed
    assert decision.denied_by == "platform"
    assert platform.calls == 1


def test_helios_can_further_restrict_platform_access():
    platform = Platform(PlatformDataDecision.allow("platform allowed"))
    policy = DataPolicy(
        platform,
        restrictions=[
            Restriction(
                DataRestrictionDecision.deny("model policy forbids execution")
            )
        ],
    )

    decision = policy.can_access(
        PRINCIPAL, RESOURCE, DataAction.QUERY_EXECUTE
    )

    assert not decision.allowed
    assert decision.denied_by == "helios"
    assert platform.calls == 0


def test_allow_requires_confirmed_platform_identity_enforcement():
    unenforced = DataPolicy(
        Platform(PlatformDataDecision(True, False, "service credential only"))
    )
    enforced = DataPolicy(Platform(PlatformDataDecision.allow("Ranger checked")))

    assert not unenforced.can_access(
        PRINCIPAL, RESOURCE, DataAction.QUERY_EXECUTE
    ).allowed
    decision = enforced.can_access(
        PRINCIPAL, RESOURCE, DataAction.QUERY_EXECUTE
    )
    assert decision.allowed
    assert decision.platform_enforced


def test_require_raises_domain_level_denial():
    with pytest.raises(DataAuthorizationDenied, match="platform authorization"):
        DataPolicy().require(
            PRINCIPAL, RESOURCE, DataAction.QUERY_EXECUTE
        )


def test_semantic_model_permission_does_not_grant_physical_data_access():
    model = authz.Resource("model", "sales", "acme")
    resource_policy = authz.Policy(
        [
            authz.Grant(
                PRINCIPAL.id, authz.Role.MODEL_CONSUMER, model
            )
        ]
    )

    assert resource_policy.can(
        PRINCIPAL, authz.Action.QUERY_EXECUTE, model
    ).allowed
    assert not DataPolicy().can_access(
        PRINCIPAL, RESOURCE, DataAction.QUERY_EXECUTE
    ).allowed


def test_mcp_query_execution_fails_closed_without_identity_propagation(
    monkeypatch,
):
    from apps.mcp import server as mcp_server

    monkeypatch.setattr(
        mcp_server.store,
        "load",
        lambda model=None: (
            {"name": model or "sales"},
            {"datasets": [], "metrics": [], "relationships": []},
        ),
    )
    monkeypatch.setattr(mcp_server, "data_policy", DataPolicy())

    result = mcp_server.run_query([], model="sales")

    assert result["error"] == "authorization_denied"
    assert "caller context" in result["message"]
