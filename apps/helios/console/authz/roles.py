"""Central role definitions, permission mapping, and resource-scoped grants."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .permissions import Action
from .resources import Resource


class Role(str, Enum):
    ORG_ADMIN = "org_admin"
    MODEL_OWNER = "model_owner"
    MODEL_EDITOR = "model_editor"
    MODEL_VIEWER = "model_viewer"
    MODEL_CONSUMER = "model_consumer"


_MODEL_READ_PERMISSIONS = frozenset(
    {
        Action.MODEL_READ,
        Action.GLOSSARY_READ,
        Action.SEMANTIC_READ,
        Action.ONTOLOGY_READ,
    }
)

_MODEL_EDIT_PERMISSIONS = _MODEL_READ_PERMISSIONS | {
    Action.DATASOURCE_READ,
    Action.MODEL_EDIT,
    Action.DISCOVERY_RUN,
    Action.GLOSSARY_EDIT,
    Action.SEMANTIC_EDIT,
    Action.ONTOLOGY_EDIT,
    Action.QUERY_COMPILE,
    Action.QUERY_EXECUTE,
}


# The sole role-to-permission mapping. Callers use Policy and never inspect it.
ROLE_PERMISSIONS: dict[Role, frozenset[Action]] = {
    Role.ORG_ADMIN: frozenset(Action),
    Role.MODEL_OWNER: _MODEL_EDIT_PERMISSIONS
    | {
        Action.MODEL_DELETE,
        Action.MODEL_PUBLISH,
    },
    Role.MODEL_EDITOR: _MODEL_EDIT_PERMISSIONS,
    Role.MODEL_VIEWER: _MODEL_READ_PERMISSIONS | {Action.DATASOURCE_READ},
    Role.MODEL_CONSUMER: _MODEL_READ_PERMISSIONS
    | {
        Action.QUERY_COMPILE,
        Action.QUERY_EXECUTE,
    },
}


@dataclass(frozen=True)
class Grant:
    """Grant one role to one principal on one explicit resource."""

    principal_id: str
    role: Role
    resource: Resource

    def __post_init__(self) -> None:
        if not self.principal_id or not self.principal_id.strip():
            raise ValueError("principal_id must not be empty")
        if not isinstance(self.role, Role):
            raise TypeError("role must be a Role")
        if not self.resource.organization_id:
            raise ValueError("grant resource must belong to an organization")
        if self.role is Role.ORG_ADMIN:
            if (
                self.resource.resource_type != "organization"
                or self.resource.resource_id != self.resource.organization_id
            ):
                raise ValueError("org_admin must be granted on an organization")
        elif self.resource.resource_type != "model":
            raise ValueError(f"{self.role.value} must be granted on a model")

    @property
    def organization_id(self) -> str:
        return self.resource.organization_id or ""

    @property
    def model_id(self) -> str | None:
        return (
            self.resource.resource_id
            if self.resource.resource_type == "model"
            else None
        )


class RoleAssignment(Grant):
    """Backward-compatible constructor for the former assignment representation."""

    def __init__(
        self,
        principal_id: str,
        role: Role,
        organization_id: str,
        model_id: str | None = None,
    ):
        resource = (
            Resource("organization", organization_id, organization_id)
            if model_id is None
            else Resource("model", model_id, organization_id)
        )
        super().__init__(principal_id, role, resource)
