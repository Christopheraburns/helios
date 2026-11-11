"""The caller a host application passes to Helios with each request.

Helios authenticates no one. The host has already decided who is calling, and
hands over whatever the configured engine identity mode needs: a username for
``impersonate``, a token for ``token``, neither for ``service``. Helios never
inspects ``attributes``; they carry host context through to host-supplied hooks
such as data restrictions and model visibility.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class IdentityMode(str, Enum):
    """Whose identity an engine runs a caller's query as. The host picks one per connection."""

    SERVICE = "service"          # the connection's own credentials; the caller is not used
    IMPERSONATE = "impersonate"  # the service connects and asks the engine to run as caller.username
    TOKEN = "token"              # the engine authenticates caller.token itself


class IdentityError(PermissionError):
    """A query cannot run as requested: the caller lacks what the connection's identity
    mode needs (a username or a token), or the connection has no service credentials."""


@dataclass(frozen=True)
class Caller:
    """Who a request runs as. The token is left out of repr so it is never logged."""

    username: str | None = None
    token: str | None = field(default=None, repr=False)
    attributes: Mapping[str, object] = field(default_factory=dict, compare=False, hash=False)

    def __post_init__(self) -> None:
        if self.username is not None and not self.username.strip():
            raise ValueError("caller username must not be blank; use None for no username")
        if self.token is not None and not self.token:
            raise ValueError("caller token must not be empty; use None for no token")
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))

    def __str__(self) -> str:
        return self.username or "anonymous caller"
