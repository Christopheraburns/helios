from helios_core import Caller, authz
from helios_core.settings import Settings
from helios_core.engines.impala import impala_config
from helios_core.data_authorization import (
    DataAction,
    DataPolicy,
    DataResource,
)


PRINCIPAL = authz.Principal(
    "workbench", "alice", authz.PrincipalKind.HUMAN
)
CALLER = Caller(username="alice")
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

    assert impala_config(Settings()) is None

    monkeypatch.setenv("WORKLOAD_USER", "workload-user")
    monkeypatch.setenv("WORKLOAD_PASSWORD", "workload-password")
    configuration = impala_config(Settings())

    assert configuration is not None
    assert configuration.user == "workload-user"
    assert configuration.password == "workload-password"


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
        CALLER, RESOURCE, DataAction.QUERY_EXECUTE
    ).allowed


def test_mcp_query_execution_fails_closed_without_identity_propagation(
    monkeypatch,
):
    from apps.helios.mcp_server import server as mcp_server

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
