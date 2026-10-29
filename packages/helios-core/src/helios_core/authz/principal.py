"""Authenticated identity consumed by Helios authorization policy.

Authentication adapters remain at application boundaries. They validate their
credentials and construct a Principal without coupling this package to HTTP,
FastAPI, bearer tokens, or a specific identity provider.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class PrincipalKind(str, Enum):
    HUMAN = "human"
    SERVICE = "service"
    AGENT = "agent"


@dataclass(frozen=True)
class Principal:
    """A successfully authenticated human, service, or agent identity."""

    issuer: str
    subject: str
    kind: PrincipalKind
    display_name: str = ""
    claims: Mapping[str, object] = field(default_factory=dict, compare=False, hash=False)

    def __post_init__(self) -> None:
        if not self.issuer or not self.issuer.strip():
            raise ValueError("principal issuer must not be empty")
        if not self.subject or not self.subject.strip():
            raise ValueError("principal subject must not be empty")
        if not isinstance(self.kind, PrincipalKind):
            raise TypeError("principal kind must be a PrincipalKind")
        object.__setattr__(self, "claims", MappingProxyType(dict(self.claims)))

    @property
    def id(self) -> str:
        """Globally stable identity key within Helios."""
        return f"{self.issuer}:{self.subject}"
