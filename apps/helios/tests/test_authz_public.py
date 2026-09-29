import pytest

from helios_core import authz


def setup_access():
    principal = authz.Principal(
        "workbench", "alice", authz.PrincipalKind.HUMAN
    )
    model = authz.Resource("model", "customer360", "acme")
    grants = [authz.Grant(principal.id, authz.Role.MODEL_EDITOR, model)]
    return principal, model, grants


def test_public_can_returns_an_authorization_decision():
    principal, model, grants = setup_access()

    allowed = authz.can(
        principal, authz.Action.MODEL_EDIT, model, grants=grants
    )
    denied = authz.can(
        principal, authz.Action.MODEL_DELETE, model, grants=grants
    )

    assert isinstance(allowed, authz.AuthorizationDecision)
    assert allowed.allowed
    assert not denied.allowed


def test_public_require_returns_decision_or_raises_domain_error():
    principal, model, grants = setup_access()

    decision = authz.require(
        principal, authz.Action.MODEL_READ, model, grants=grants
    )
    assert decision.allowed

    with pytest.raises(authz.AuthorizationDenied) as exc:
        authz.require(
            principal, authz.Action.MODEL_PUBLISH, model, grants=grants
        )
    assert exc.value.resource == model
    assert exc.value.principal == principal


def test_configured_policy_exposes_same_stable_interface():
    principal, model, grants = setup_access()
    policy = authz.Policy(grants)

    assert policy.can(principal, "semantic.edit", model).allowed
    with pytest.raises(authz.AuthorizationDenied):
        policy.require(principal, "organization.manage", model)


def test_public_interface_is_not_coupled_to_fastapi():
    assert "fastapi" not in authz.__dict__
