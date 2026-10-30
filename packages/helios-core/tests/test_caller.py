"""helios_core.Caller replaces Principal in library signatures (burndown 2.1)."""
import ast
import logging
from pathlib import Path

import pytest

from helios_core import Caller, IdentityMode
from helios_core.data_authorization import (
    DataAction,
    DataAuthorizationDenied,
    DataPolicy,
    DataResource,
)

SOURCE = Path(__file__).resolve().parents[1] / "src" / "helios_core"
# Helios's own identity code, which moves to the old console in 2.6.
LEAVING = ("authz/", "authorization.py", "identity.py", "delegation.py", "rbac.py", "metadata/users.py", "_pending/")


def test_every_field_is_optional_for_service_mode():
    caller = Caller()
    assert (caller.username, caller.token, dict(caller.attributes)) == (None, None, {})
    assert str(caller) == "anonymous caller"


def test_token_is_never_in_repr_or_str(caplog):
    caller = Caller(username="alice", token="s3cr3t-jwt", attributes={"team": "finance"})
    with caplog.at_level(logging.INFO):
        logging.getLogger(__name__).info("running as %s / %r", caller, caller)
    assert "s3cr3t-jwt" not in repr(caller)
    assert "s3cr3t-jwt" not in str(caller)
    assert "s3cr3t-jwt" not in caplog.text
    assert "alice" in caplog.text


def test_attributes_are_read_only_and_copied():
    source = {"team": "finance"}
    caller = Caller(username="alice", attributes=source)
    source["team"] = "sales"
    assert caller.attributes["team"] == "finance"
    with pytest.raises(TypeError):
        caller.attributes["team"] = "sales"  # type: ignore[index]


def test_blank_values_are_rejected():
    with pytest.raises(ValueError, match="username"):
        Caller(username="  ")
    with pytest.raises(ValueError, match="token"):
        Caller(token="")


def test_callers_are_equal_by_username_and_token_only():
    assert Caller("alice", attributes={"a": 1}) == Caller("alice", attributes={"b": 2})
    assert Caller("alice", token="x") != Caller("alice", token="y")
    assert len({Caller("alice"), Caller("alice")}) == 1


def test_data_policy_takes_a_caller():
    policy = DataPolicy()
    resource = DataResource("warehouse", "sales.orders", IdentityMode.IMPERSONATE)

    assert policy.can_access(Caller("alice"), resource, DataAction.QUERY_EXECUTE).allowed
    denied = policy.can_access(Caller(), resource, DataAction.QUERY_EXECUTE)
    assert not denied.allowed and "no username" in denied.reason

    with pytest.raises(DataAuthorizationDenied, match="^anonymous caller may not") as raised:
        policy.require(Caller(token="s3cr3t-jwt"), resource, DataAction.QUERY_EXECUTE)
    assert "s3cr3t-jwt" not in str(raised.value)


def test_no_library_signature_names_principal():
    offenders = []
    for path in sorted(SOURCE.rglob("*.py")):
        relative = path.relative_to(SOURCE).as_posix()
        if relative.startswith(LEAVING):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            name = node.id if isinstance(node, ast.Name) else getattr(node, "attr", None)
            if name == "Principal" or (isinstance(node, ast.alias) and node.name == "Principal"):
                offenders.append(f"{relative}:{getattr(node, 'lineno', '?')}")
    assert not offenders, offenders
