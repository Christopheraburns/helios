"""Boundary between Helios policy and physical platform data enforcement.

Helios may add restrictions, but this policy returns allow only after a platform
adapter confirms that access is enforced for the relevant identity. The default
platform adapter is unavailable and denies every operation.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Protocol

from helios_core.identity import Principal


class DataAction(str, Enum):
    METADATA_READ = "metadata.read"
    PROFILE = "profile"
    QUERY_EXECUTE = "query.execute"


@dataclass(frozen=True)
class DataResource:
    """Physical data addressed through a Helios DataSource."""

    data_source_id: str
    asset: str | None = None

    def __post_init__(self) -> None:
        if not self.data_source_id or not self.data_source_id.strip():
            raise ValueError("data_source_id must not be empty")
        if self.asset is not None and not self.asset.strip():
            raise ValueError("data asset must not be empty")


@dataclass(frozen=True)
class PlatformDataDecision:
    """Result supplied by the underlying platform enforcement adapter."""

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
    """A Helios restriction can deny, but cannot grant platform access."""

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
    """Final composed decision returned to a Helios caller."""

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
    """Adapter implemented only when Cloudera can enforce the relevant identity."""

    def can_access(
        self,
        principal: Principal,
        resource: DataResource,
        action: str | DataAction,
    ) -> PlatformDataDecision: ...


class HeliosDataRestriction(Protocol):
    """Optional Helios-specific deny-only policy."""

    def can_access(
        self,
        principal: Principal,
        resource: DataResource,
        action: str | DataAction,
    ) -> DataRestrictionDecision: ...


class DataAuthorizer(Protocol):
    """Public physical-data authorization boundary."""

    def can_access(
        self,
        principal: Principal,
        resource: DataResource,
        action: str | DataAction,
    ) -> DataAuthorizationDecision: ...


class UnavailablePlatformDataAuthorizer:
    """Safe default until caller identity propagation is implemented."""

    def can_access(
        self,
        principal: Principal,
        resource: DataResource,
        action: str | DataAction,
    ) -> PlatformDataDecision:
        return PlatformDataDecision.deny(
            "underlying platform authorization is unavailable for this identity"
        )


class ImpalaProxyDataAuthorizer:
    """Confirm that execution will use Impala's Ranger-backed proxy identity."""

    def __init__(self, enabled: bool):
        self.enabled = enabled

    def can_access(
        self,
        principal: Principal,
        resource: DataResource,
        action: str | DataAction,
    ) -> PlatformDataDecision:
        if action != DataAction.QUERY_EXECUTE and action != DataAction.QUERY_EXECUTE.value:
            return PlatformDataDecision.deny(
                "Impala delegation only enforces query execution"
            )
        if not self.enabled:
            return PlatformDataDecision.deny(
                "Impala proxy-user delegation is not enabled"
            )
        if not principal.subject.strip():
            return PlatformDataDecision.deny(
                "the delegated principal has no workload identity"
            )
        return PlatformDataDecision.allow(
            "Impala will verify the delegated effective user before execution"
        )


class DataAuthorizationDenied(PermissionError):
    def __init__(
        self,
        principal: Principal,
        resource: DataResource,
        action: str | DataAction,
        reason: str,
    ):
        self.principal = principal
        self.resource = resource
        self.action = action
        self.reason = reason
        action_name = action.value if isinstance(action, DataAction) else action
        super().__init__(
            f"{principal.id} may not {action_name} physical data through "
            f"{resource.data_source_id}: {reason}"
        )


class DataPolicy:
    """Compose Helios deny-only restrictions with platform enforcement."""

    def __init__(
        self,
        platform: PlatformDataAuthorizer | None = None,
        restrictions: Iterable[HeliosDataRestriction] = (),
    ):
        self.platform = platform or UnavailablePlatformDataAuthorizer()
        self.restrictions = tuple(restrictions)

    def can_access(
        self,
        principal: Principal,
        resource: DataResource,
        action: str | DataAction,
    ) -> DataAuthorizationDecision:
        for restriction in self.restrictions:
            decision = restriction.can_access(principal, resource, action)
            if not decision.allowed:
                return DataAuthorizationDecision.deny(
                    decision.reason, denied_by="helios"
                )

        platform = self.platform.can_access(principal, resource, action)
        if not platform.allowed:
            return DataAuthorizationDecision.deny(
                platform.reason, denied_by="platform"
            )
        if not platform.identity_enforced:
            return DataAuthorizationDecision.deny(
                "platform adapter did not confirm identity enforcement",
                denied_by="platform",
            )
        return DataAuthorizationDecision.allow(platform.reason)

    def require(
        self,
        principal: Principal,
        resource: DataResource,
        action: str | DataAction,
    ) -> DataAuthorizationDecision:
        decision = self.can_access(principal, resource, action)
        if not decision.allowed:
            raise DataAuthorizationDenied(
                principal, resource, action, decision.reason
            )
        return decision

    # Compatibility with the initial boundary.
    def authorize_data(
        self,
        principal: Principal,
        action: str | DataAction,
        resource: DataResource,
    ) -> DataAuthorizationDecision:
        return self.can_access(principal, resource, action)
