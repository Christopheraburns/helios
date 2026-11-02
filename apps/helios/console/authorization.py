"""Compatibility imports; new code should use :mod:`apps.helios.console.authz`."""

from apps.helios.console.authz import (
    AuthorizationDecision as ResourceAuthorizationDecision,
    AuthorizationDenied as ResourceAuthorizationError,
    Authorizer as ResourceAuthorizer,
    HeliosResource,
    Principal,
)


def require_resource_access(
    authorizer: ResourceAuthorizer,
    principal: Principal,
    action: str,
    resource: HeliosResource,
) -> None:
    """Compatibility wrapper around a configured authorization policy."""
    check = getattr(authorizer, "can", None) or getattr(authorizer, "authorize")
    decision = check(principal, action, resource)
    if not decision.allowed:
        raise ResourceAuthorizationError(
            principal, action, resource, decision.reason
        )


__all__ = [
    "HeliosResource",
    "ResourceAuthorizationDecision",
    "ResourceAuthorizationError",
    "ResourceAuthorizer",
    "require_resource_access",
]
