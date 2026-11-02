"""Compatibility imports; new code should use :mod:`apps.helios.console.authz`."""

from apps.helios.console.authz import (
    Action,
    Grant,
    Policy as RbacAuthorizer,
    Role,
)
from apps.helios.console.authz.roles import ROLE_PERMISSIONS, RoleAssignment

__all__ = [
    "Action",
    "Grant",
    "ROLE_PERMISSIONS",
    "RbacAuthorizer",
    "Role",
    "RoleAssignment",
]
