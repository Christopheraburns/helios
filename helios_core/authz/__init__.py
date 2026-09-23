"""Stable public interface for Helios resource authorization.

Components should construct a Policy from persisted grants, then call
``policy.can(...)`` or ``policy.require(...)``. The package-level ``can`` and
``require`` helpers support one-shot checks. Callers never inspect role mappings.
"""
from .permissions import Action
from .policy import (
    AuthorizationDecision,
    AuthorizationDenied,
    Authorizer,
    Policy,
    can,
    require,
)
from .principal import Principal, PrincipalKind
from .resources import HeliosResource, Resource
from .roles import Grant, Role

__all__ = [
    "Action",
    "AuthorizationDecision",
    "AuthorizationDenied",
    "Authorizer",
    "Grant",
    "HeliosResource",
    "Policy",
    "Principal",
    "PrincipalKind",
    "Resource",
    "Role",
    "can",
    "require",
]
