import pytest

from helios_core.authorization import HeliosResource
from helios_core.identity import Principal, PrincipalKind
from helios_core.rbac import (
    Action,
    Grant,
    RbacAuthorizer,
    Role,
    RoleAssignment,
)


PRINCIPAL = Principal("test", "principal", PrincipalKind.HUMAN)
ORG_ID = "acme"
MODEL_ID = "sales"

EXPECTED_PERMISSIONS = {
    Role.ORG_ADMIN: set(Action),
    Role.MODEL_OWNER: {
        Action.DATASOURCE_READ,
        Action.MODEL_READ,
        Action.MODEL_EDIT,
        Action.MODEL_DELETE,
        Action.MODEL_PUBLISH,
        Action.DISCOVERY_RUN,
        Action.GLOSSARY_READ,
        Action.GLOSSARY_EDIT,
        Action.SEMANTIC_READ,
        Action.SEMANTIC_EDIT,
        Action.ONTOLOGY_READ,
        Action.ONTOLOGY_EDIT,
        Action.QUERY_COMPILE,
        Action.QUERY_EXECUTE,
    },
    Role.MODEL_EDITOR: {
        Action.DATASOURCE_READ,
        Action.MODEL_READ,
        Action.MODEL_EDIT,
        Action.DISCOVERY_RUN,
        Action.GLOSSARY_READ,
        Action.GLOSSARY_EDIT,
        Action.SEMANTIC_READ,
        Action.SEMANTIC_EDIT,
        Action.ONTOLOGY_READ,
        Action.ONTOLOGY_EDIT,
        Action.QUERY_COMPILE,
        Action.QUERY_EXECUTE,
    },
    Role.MODEL_VIEWER: {
        Action.DATASOURCE_READ,
        Action.MODEL_READ,
        Action.GLOSSARY_READ,
        Action.SEMANTIC_READ,
        Action.ONTOLOGY_READ,
    },
    Role.MODEL_CONSUMER: {
        Action.MODEL_READ,
        Action.GLOSSARY_READ,
        Action.SEMANTIC_READ,
        Action.ONTOLOGY_READ,
        Action.QUERY_COMPILE,
        Action.QUERY_EXECUTE,
    },
}


def resource_for(action: Action) -> HeliosResource:
    prefix = action.value.split(".", 1)[0]
    if prefix == "organization":
        return HeliosResource("organization", ORG_ID, ORG_ID)
    if prefix == "datasource":
        return HeliosResource("datasource", "warehouse", ORG_ID, MODEL_ID)
    if prefix == "model":
        return HeliosResource("model", MODEL_ID, ORG_ID)
    if prefix in {"discovery", "query"}:
        return HeliosResource("model", MODEL_ID, ORG_ID)
    return HeliosResource(prefix, f"{prefix}-1", ORG_ID, MODEL_ID)


def assignment_for(role: Role) -> Grant:
    resource = (
        HeliosResource("organization", ORG_ID, ORG_ID)
        if role is Role.ORG_ADMIN
        else HeliosResource("model", MODEL_ID, ORG_ID)
    )
    return Grant(
        principal_id=PRINCIPAL.id,
        role=role,
        resource=resource,
    )


@pytest.mark.parametrize("role", list(Role))
def test_every_role_allows_and_denies_every_initial_action(role):
    authorizer = RbacAuthorizer([assignment_for(role)])

    decisions = {
        action: authorizer.authorize(
            PRINCIPAL, action.value, resource_for(action)
        ).allowed
        for action in Action
    }

    assert {action for action, allowed in decisions.items() if allowed} == (
        EXPECTED_PERMISSIONS[role]
    )
    assert {action for action, allowed in decisions.items() if not allowed} == (
        set(Action) - EXPECTED_PERMISSIONS[role]
    )


@pytest.mark.parametrize(
    "role",
    [
        Role.MODEL_OWNER,
        Role.MODEL_EDITOR,
        Role.MODEL_VIEWER,
        Role.MODEL_CONSUMER,
    ],
)
def test_model_roles_are_denied_on_another_model(role):
    authorizer = RbacAuthorizer([assignment_for(role)])
    other_model = HeliosResource("model", "finance", ORG_ID)

    assert not authorizer.authorize(
        PRINCIPAL, Action.MODEL_READ.value, other_model
    ).allowed


def test_org_admin_is_denied_outside_assigned_organization():
    authorizer = RbacAuthorizer([assignment_for(Role.ORG_ADMIN)])
    other_org_model = HeliosResource("model", MODEL_ID, "other-org")

    assert not authorizer.authorize(
        PRINCIPAL, Action.MODEL_DELETE.value, other_org_model
    ).allowed


def test_unknown_actions_and_unscoped_resources_are_denied():
    authorizer = RbacAuthorizer([assignment_for(Role.ORG_ADMIN)])

    assert not authorizer.authorize(
        PRINCIPAL, "model.teleport", resource_for(Action.MODEL_READ)
    ).allowed
    assert not authorizer.authorize(
        PRINCIPAL, Action.MODEL_READ.value, HeliosResource("model", MODEL_ID)
    ).allowed


def test_grant_enforces_role_scope():
    with pytest.raises(ValueError, match="organization"):
        Grant(
            PRINCIPAL.id,
            Role.ORG_ADMIN,
            HeliosResource("model", MODEL_ID, ORG_ID),
        )
    with pytest.raises(ValueError, match="model"):
        Grant(
            PRINCIPAL.id,
            Role.MODEL_EDITOR,
            HeliosResource("organization", ORG_ID, ORG_ID),
        )


def test_former_role_assignment_constructor_remains_compatible():
    assignment = RoleAssignment(
        PRINCIPAL.id, Role.MODEL_EDITOR, ORG_ID, MODEL_ID
    )

    assert assignment.resource == HeliosResource("model", MODEL_ID, ORG_ID)


def test_principal_can_have_different_roles_on_different_models():
    alice = Principal("test", "alice", PrincipalKind.HUMAN)
    customer = HeliosResource("model", "customer360", ORG_ID)
    finance = HeliosResource("model", "finance", ORG_ID)
    authorizer = RbacAuthorizer(
        [
            Grant(alice.id, Role.MODEL_EDITOR, customer),
            Grant(alice.id, Role.MODEL_VIEWER, finance),
        ]
    )

    assert authorizer.authorize(
        alice, Action.MODEL_EDIT.value, customer
    ).allowed
    assert authorizer.authorize(
        alice, Action.MODEL_READ.value, finance
    ).allowed
    assert not authorizer.authorize(
        alice, Action.MODEL_EDIT.value, finance
    ).allowed


def test_model_specific_grants_do_not_leak_between_principals_or_models():
    alice = Principal("test", "alice", PrincipalKind.HUMAN)
    bob = Principal("test", "bob", PrincipalKind.HUMAN)
    customer = HeliosResource("model", "customer360", ORG_ID)
    finance = HeliosResource("model", "finance", ORG_ID)
    authorizer = RbacAuthorizer(
        [
            Grant(alice.id, Role.MODEL_EDITOR, customer),
            Grant(bob.id, Role.MODEL_CONSUMER, finance),
        ]
    )

    assert not authorizer.authorize(
        alice, Action.MODEL_READ.value, finance
    ).allowed
    assert authorizer.authorize(
        bob, Action.QUERY_EXECUTE.value, finance
    ).allowed
    assert not authorizer.authorize(
        bob, Action.QUERY_EXECUTE.value, customer
    ).allowed


def test_org_admin_inherits_capabilities_for_resources_in_organization():
    chris = Principal("test", "chris", PrincipalKind.HUMAN)
    acme = HeliosResource("organization", ORG_ID, ORG_ID)
    customer = HeliosResource("model", "customer360", ORG_ID)
    glossary = HeliosResource(
        "glossary", "customer-terms", ORG_ID, "customer360"
    )
    authorizer = RbacAuthorizer(
        [
            Grant(chris.id, Role.ORG_ADMIN, acme),
            Grant(chris.id, Role.MODEL_OWNER, customer),
        ]
    )

    assert authorizer.authorize(
        chris, Action.MODEL_CREATE.value, customer
    ).allowed
    assert authorizer.authorize(
        chris, Action.GLOSSARY_EDIT.value, glossary
    ).allowed


def test_same_resource_id_in_another_organization_is_denied():
    alice = Principal("test", "alice", PrincipalKind.HUMAN)
    acme_finance = HeliosResource("model", "finance", "acme")
    other_finance = HeliosResource("model", "finance", "other")
    authorizer = RbacAuthorizer(
        [Grant(alice.id, Role.MODEL_VIEWER, acme_finance)]
    )

    assert authorizer.authorize(
        alice, Action.MODEL_READ.value, acme_finance
    ).allowed
    assert not authorizer.authorize(
        alice, Action.MODEL_READ.value, other_finance
    ).allowed
