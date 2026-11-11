"""Whether a caller may reach physical data through Helios.

Helios grants nothing. A DataPolicy allows an operation only when a platform
authorizer confirms that the warehouse itself will enforce the caller's identity,
so the warehouse's own permissions decide. The host may add deny-only restrictions
on top; a restriction can refuse what the platform allows, never the reverse.

The default platform authorizer, EngineIdentityAuthorizer, reads the identity mode
of the connection that reaches the data: ``impersonate`` and ``token`` run the query
as the caller, so it allows; ``service`` runs it as the connection's own identity,
so it denies. A host that deliberately runs callers' queries as a service account
supplies its own platform authorizer.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from helios_core.caller import Caller, IdentityMode


class DataAction(str, Enum):
    METADATA_READ = "metadata.read"
    PROFILE = "profile"
    QUERY_EXECUTE = "query.execute"


@dataclass(frozen=True)
class DataResource:
    """Physical data addressed through a Helios DataSource. ``identity_mode`` is the
    mode of the connection that reaches it (``Engine.identity_mode``)."""

    data_source_id: str
    asset: str | None = None
    identity_mode: IdentityMode = IdentityMode.SERVICE

    def __post_init__(self) -> None:
        if not self.data_source_id or not self.data_source_id.strip():
            raise ValueError("data_source_id must not be empty")
        if self.asset is not None and not self.asset.strip():
            raise ValueError("data asset must not be empty")
        object.__setattr__(self, "identity_mode", IdentityMode(self.identity_mode))


@dataclass(frozen=True)
class PlatformDataDecision:
    """A platform authorizer's answer: allowed, and whether the caller's identity is enforced."""

    allowed: bool
    identity_enforced: bool
    reason: str = ""

    @classmethod
    def allow(cls, reason: str = "") -> PlatformDataDecision:
        return cls(True, True, reason)

    @classmethod
    def deny(cls, reason: str = "") -> PlatformDataDecision:
        return cls(False, False, reason)


@dataclass(frozen=True)
class DataRestrictionDecision:
    """A restriction can deny, but cannot grant platform access."""

    allowed: bool
    reason: str = ""

    @classmethod
    def allow(cls) -> DataRestrictionDecision:
        return cls(True)

    @classmethod
    def deny(cls, reason: str) -> DataRestrictionDecision:
        return cls(False, reason)


@dataclass(frozen=True)
class DataAuthorizationDecision:
    """The composed decision. ``denied_by`` is ``"restriction"`` or ``"platform"``."""

    allowed: bool
    reason: str = ""
    platform_enforced: bool = False
    denied_by: str | None = None

    @classmethod
    def allow(cls, reason: str = "") -> DataAuthorizationDecision:
        return cls(True, reason, platform_enforced=True)

    @classmethod
    def deny(
        cls, reason: str = "", denied_by: str | None = None
    ) -> DataAuthorizationDecision:
        return cls(False, reason, platform_enforced=False, denied_by=denied_by)


class PlatformDataAuthorizer(Protocol):
    """Confirms that the platform will enforce the caller's identity for this access."""

    def can_access(
        self,
        caller: Caller,
        resource: DataResource,
        action: str | DataAction,
    ) -> PlatformDataDecision: ...


class DataRestriction(Protocol):
    """A deny-only rule the host supplies (by model, team, time of day, ...)."""

    def can_access(
        self,
        caller: Caller,
        resource: DataResource,
        action: str | DataAction,
    ) -> DataRestrictionDecision: ...


class EngineIdentityAuthorizer:
    """Allow when the connection's identity mode makes the engine run as the caller.

    Only ``actions`` are allowed: by default query execution, the one operation
    Helios runs with the caller's identity (harvesting and profiling run as the
    service)."""

    def __init__(self, actions: Iterable[str | DataAction] = (DataAction.QUERY_EXECUTE,)):
        self.actions = frozenset(DataAction(action) for action in actions)

    def can_access(
        self,
        caller: Caller,
        resource: DataResource,
        action: str | DataAction,
    ) -> PlatformDataDecision:
        name = action.value if isinstance(action, DataAction) else action
        if name not in {allowed.value for allowed in self.actions}:
            return PlatformDataDecision.deny(f"{name} does not run as the caller")
        mode = resource.identity_mode
        if mode is IdentityMode.IMPERSONATE and caller.username is None:
            return PlatformDataDecision.deny("the caller has no username to run the query as")
        if mode is IdentityMode.TOKEN and caller.token is None:
            return PlatformDataDecision.deny("the caller has no token to run the query with")
        if mode is IdentityMode.SERVICE:
            return PlatformDataDecision.deny(
                "the connection runs queries as its own service identity, so the "
                "warehouse cannot enforce the caller's permissions"
            )
        return PlatformDataDecision.allow(
            f"the engine runs the query as the caller ({mode.value} mode)"
        )


class DataAuthorizationDenied(PermissionError):
    def __init__(
        self,
        caller: Caller,
        resource: DataResource,
        action: str | DataAction,
        reason: str,
    ):
        self.caller = caller
        self.resource = resource
        self.action = action
        self.reason = reason
        action_name = action.value if isinstance(action, DataAction) else action
        super().__init__(
            f"{caller} may not {action_name} physical data through "
            f"{resource.data_source_id}: {reason}"
        )


class DataPolicy:
    """Host restrictions (deny-only) composed with platform enforcement."""

    def __init__(
        self,
        platform: PlatformDataAuthorizer | None = None,
        restrictions: Iterable[DataRestriction] = (),
    ):
        self.platform = platform or EngineIdentityAuthorizer()
        self.restrictions = tuple(restrictions)

    def can_access(
        self,
        caller: Caller,
        resource: DataResource,
        action: str | DataAction,
    ) -> DataAuthorizationDecision:
        for restriction in self.restrictions:
            decision = restriction.can_access(caller, resource, action)
            if not decision.allowed:
                return DataAuthorizationDecision.deny(
                    decision.reason, denied_by="restriction"
                )

        platform = self.platform.can_access(caller, resource, action)
        if not platform.allowed:
            return DataAuthorizationDecision.deny(
                platform.reason, denied_by="platform"
            )
        if not platform.identity_enforced:
            return DataAuthorizationDecision.deny(
                "platform authorizer did not confirm identity enforcement",
                denied_by="platform",
            )
        return DataAuthorizationDecision.allow(platform.reason)

    def require(
        self,
        caller: Caller,
        resource: DataResource,
        action: str | DataAction,
    ) -> DataAuthorizationDecision:
        decision = self.can_access(caller, resource, action)
        if not decision.allowed:
            raise DataAuthorizationDenied(
                caller, resource, action, decision.reason
            )
        return decision
