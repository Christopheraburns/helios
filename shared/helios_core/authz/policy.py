"""Reusable Helios resource-authorization policy and public decisions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from .permissions import Action
from .principal import Principal
from .resources import Resource
from .roles import Grant, ROLE_PERMISSIONS


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    reason: str = ""

    @classmethod
    def allow(cls, reason: str = "") -> AuthorizationDecision:
        return cls(True, reason)

    @classmethod
    def deny(cls, reason: str = "") -> AuthorizationDecision:
        return cls(False, reason)


class Authorizer(Protocol):
    """Port shared by web, MCP, and background components."""

    def can(
        self,
        principal: Principal,
        action: str | Action,
        resource: Resource,
    ) -> AuthorizationDecision: ...


class AuthorizationDenied(PermissionError):
    """Domain-level denial with no transport or FastAPI dependency."""

    def __init__(
        self,
        principal: Principal,
        action: str | Action,
        resource: Resource,
        reason: str = "",
    ):
        self.principal = principal
        self.action = action
        self.resource = resource
        self.reason = reason
        action_name = action.value if isinstance(action, Action) else action
        detail = f": {reason}" if reason else ""
        super().__init__(
            f"{principal.id} may not {action_name} "
            f"{resource.resource_type}/{resource.resource_id}{detail}"
        )


class Policy:
    """Evaluate centralized role permissions and resource-scoped grants."""

    def __init__(self, grants: Iterable[Grant] = ()):
        self._grants = tuple(grants)

    def can(
        self,
        principal: Principal,
        action: str | Action,
        resource: Resource,
    ) -> AuthorizationDecision:
        try:
            domain_action = Action(action)
        except ValueError:
            return AuthorizationDecision.deny(f"unknown Helios action {action!r}")

        if not resource.organization_id:
            return AuthorizationDecision.deny(
                "authorization requires an organization-scoped resource"
            )

        for grant in self._grants:
            if (
                grant.principal_id != principal.id
                or grant.organization_id != resource.organization_id
                or domain_action not in ROLE_PERMISSIONS[grant.role]
            ):
                continue
            if grant.resource.resource_type == "organization":
                return AuthorizationDecision.allow(
                    f"allowed by {grant.role.value} on organization "
                    f"{grant.resource.resource_id}"
                )
            if grant.model_id == _resource_model_id(resource):
                return AuthorizationDecision.allow(
                    f"allowed by {grant.role.value} on model {grant.model_id}"
                )

        return AuthorizationDecision.deny(
            f"{principal.id} has no role granting {domain_action.value} "
            f"on {resource.resource_type}/{resource.resource_id}"
        )

    def require(
        self,
        principal: Principal,
        action: str | Action,
        resource: Resource,
    ) -> AuthorizationDecision:
        decision = self.can(principal, action, resource)
        if not decision.allowed:
            raise AuthorizationDenied(principal, action, resource, decision.reason)
        return decision

    # Compatibility with the original ResourceAuthorizer protocol.
    def authorize(
        self,
        principal: Principal,
        action: str | Action,
        resource: Resource,
    ) -> AuthorizationDecision:
        return self.can(principal, action, resource)


def can(
    principal: Principal,
    action: str | Action,
    resource: Resource,
    grants: Iterable[Grant] = (),
) -> AuthorizationDecision:
    """One-shot public authorization check for callers without a configured Policy."""
    return Policy(grants).can(principal, action, resource)


def require(
    principal: Principal,
    action: str | Action,
    resource: Resource,
    grants: Iterable[Grant] = (),
) -> AuthorizationDecision:
    """One-shot check that raises AuthorizationDenied when access is denied."""
    return Policy(grants).require(principal, action, resource)


def _resource_model_id(resource: Resource) -> str | None:
    if resource.resource_type == "model":
        return resource.resource_id
    return resource.model_id


# Compatibility names retained for existing core callers.
ResourceAuthorizationDecision = AuthorizationDecision
ResourceAuthorizationError = AuthorizationDenied
ResourceAuthorizer = Authorizer
RbacAuthorizer = Policy
