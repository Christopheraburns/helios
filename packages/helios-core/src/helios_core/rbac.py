"""Compatibility imports; new code should use :mod:`helios_core.authz`."""

from helios_core.authz import (
    Action,
    Grant,
    Policy as RbacAuthorizer,
    Role,
)
from helios_core.authz.roles import ROLE_PERMISSIONS, RoleAssignment

__all__ = [
    "Action",
    "Grant",
    "ROLE_PERMISSIONS",
    "RbacAuthorizer",
    "Role",
    "RoleAssignment",
]
