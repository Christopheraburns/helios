"""DataPolicy: the warehouse decides, through the caller's identity; the host may only deny more (burndown 2.3)."""
import pytest

from helios_core import Caller, IdentityMode
from helios_core.data_authorization import (
    DataAction,
    DataAuthorizationDenied,
    DataPolicy,
    DataResource,
    DataRestrictionDecision,
    EngineIdentityAuthorizer,
    PlatformDataDecision,
)

ALICE = Caller("alice", token="alice-jwt")


def orders(mode=IdentityMode.SERVICE):
    return DataResource("warehouse", "sales.orders", mode)


class Platform:
    def __init__(self, decision):
        self.decision = decision
        self.calls = 0

    def can_access(self, caller, resource, action):
        self.calls += 1
        return self.decision


class Restriction:
    def __init__(self, decision):
        self.decision = decision

    def can_access(self, caller, resource, action):
        return self.decision


@pytest.mark.parametrize(
    ("mode", "caller", "allowed"),
    [
        (IdentityMode.SERVICE, ALICE, False),
        (IdentityMode.IMPERSONATE, ALICE, True),
        (IdentityMode.IMPERSONATE, Caller(token="alice-jwt"), False),
        (IdentityMode.TOKEN, ALICE, True),
        (IdentityMode.TOKEN, Caller("alice"), False),
        (IdentityMode.TOKEN, Caller(), False),
    ],
)
def test_default_policy_allows_only_modes_that_run_as_the_caller(mode, caller, allowed):
    decision = DataPolicy().can_access(caller, orders(mode), DataAction.QUERY_EXECUTE)
    assert decision.allowed is allowed
    assert decision.platform_enforced is allowed
    assert decision.denied_by == (None if allowed else "platform")


def test_resources_default_to_service_mode_so_the_default_policy_fails_closed():
    decision = DataPolicy().can_access(ALICE, DataResource("warehouse"), DataAction.QUERY_EXECUTE)
    assert not decision.allowed
    assert "service identity" in decision.reason


def test_only_query_execution_runs_as_the_caller_by_default():
    for action in (DataAction.PROFILE, DataAction.METADATA_READ, "drop.table"):
        decision = DataPolicy().can_access(ALICE, orders(IdentityMode.TOKEN), action)
        assert not decision.allowed, action
        assert "does not run as the caller" in decision.reason
    assert DataPolicy().can_access(ALICE, orders(IdentityMode.TOKEN), "query.execute").allowed

    wider = DataPolicy(EngineIdentityAuthorizer(actions=[DataAction.QUERY_EXECUTE, DataAction.PROFILE]))
    assert wider.can_access(ALICE, orders(IdentityMode.TOKEN), DataAction.PROFILE).allowed


def test_a_restriction_cannot_override_a_platform_denial():
    platform = Platform(PlatformDataDecision.deny("Ranger denied access"))
    policy = DataPolicy(platform, restrictions=[Restriction(DataRestrictionDecision.allow())])

    decision = policy.can_access(ALICE, orders(), DataAction.QUERY_EXECUTE)

    assert not decision.allowed
    assert decision.denied_by == "platform"
    assert platform.calls == 1


def test_a_restriction_denies_before_the_platform_is_asked():
    platform = Platform(PlatformDataDecision.allow("platform allowed"))
    policy = DataPolicy(
        platform,
        restrictions=[Restriction(DataRestrictionDecision.deny("model policy forbids execution"))],
    )

    decision = policy.can_access(ALICE, orders(IdentityMode.IMPERSONATE), DataAction.QUERY_EXECUTE)

    assert not decision.allowed
    assert decision.denied_by == "restriction"
    assert decision.reason == "model policy forbids execution"
    assert platform.calls == 0


def test_allow_requires_confirmed_identity_enforcement():
    unenforced = DataPolicy(Platform(PlatformDataDecision(True, False, "service credential only")))
    assert not unenforced.can_access(ALICE, orders(), DataAction.QUERY_EXECUTE).allowed


def test_a_host_may_accept_service_mode_with_its_own_platform_authorizer():
    class TrustedServiceAccount:
        def can_access(self, caller, resource, action):
            return PlatformDataDecision.allow("the host accepts service-account execution")

    assert DataPolicy(TrustedServiceAccount()).can_access(ALICE, orders(), DataAction.QUERY_EXECUTE).allowed


def test_require_raises_without_the_token():
    with pytest.raises(DataAuthorizationDenied, match="^alice may not query.execute physical data through warehouse") as raised:
        DataPolicy().require(ALICE, orders(), DataAction.QUERY_EXECUTE)
    assert raised.value.caller == ALICE
    assert "alice-jwt" not in str(raised.value)
    assert DataPolicy().require(ALICE, orders(IdentityMode.TOKEN), DataAction.QUERY_EXECUTE).allowed


def test_resource_identity_mode_accepts_the_setting_string():
    assert DataResource("warehouse", identity_mode="token").identity_mode is IdentityMode.TOKEN
    with pytest.raises(ValueError):
        DataResource("warehouse", identity_mode="kerberos")
