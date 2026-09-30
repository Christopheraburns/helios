"""Register principals and grant them access.

Run with ``python -m helios_core.metadata.users``.

There is no REST or UI path for this: organization membership and model grants
are repository-level operations, so this is the supported way to onboard a user.

The principal id must match what the console derives from the SSO headers --
``<issuer>:<remote-user>``, with the issuer hardcoded to ``cloudera-workbench``
in ``apps/helios/console/api.py``. Principals are never created on first sight,
so a mismatch does not fail loudly: the user authenticates and gets an empty
console. Every command prints the id it used so that can be checked.
"""

from __future__ import annotations

import argparse
import sys

from helios_core import authz

from .repository import PrincipalRecord
from .sqlite import SQLiteMetadataRepository

DEFAULT_ISSUER = "cloudera-workbench"

_ORG_ROLES = {authz.Role.ORG_ADMIN}
_MODEL_ROLES = {role for role in authz.Role if role not in _ORG_ROLES}


def principal_id(username: str, issuer: str = DEFAULT_ISSUER) -> str:
    return f"{issuer}:{username}"


def _fail(message: str) -> None:
    raise SystemExit(f"error: {message}")


def _parse_role(value: str) -> authz.Role:
    try:
        return authz.Role(value.strip().lower())
    except ValueError:
        options = ", ".join(sorted(role.value for role in authz.Role))
        _fail(f"unknown role {value!r}; expected one of {options}")
        raise  # unreachable; keeps the type checker happy


def _check_username(username: str) -> str:
    cleaned = username.strip()
    if not cleaned:
        _fail("username must not be empty")
    # A colon would split wrongly when the console rebuilds issuer:subject.
    if ":" in cleaned or any(character.isspace() for character in cleaned):
        _fail(f"username {username!r} must not contain a colon or whitespace")
    return cleaned


def _print_grants(repository: SQLiteMetadataRepository, identifier: str) -> None:
    grants = repository.grants_for_principal(identifier)
    if not grants:
        print("  grants: none -- this user will see an empty console")
        return
    print("  grants:")
    for grant in sorted(grants, key=lambda g: (g.resource.resource_type, g.resource.resource_id)):
        resource = grant.resource
        print(f"    {grant.role.value} on {resource.resource_type} {resource.resource_id}")


def add(arguments: argparse.Namespace) -> None:
    username = _check_username(arguments.username)
    identifier = principal_id(username, arguments.issuer)
    role = _parse_role(arguments.role) if arguments.role else None

    if role is not None:
        if role in _ORG_ROLES and not arguments.organization:
            _fail(f"role {role.value} is an organization membership; pass --organization")
        if role in _MODEL_ROLES and not arguments.model:
            _fail(f"role {role.value} is granted per model; pass --model")

    repository = SQLiteMetadataRepository()
    repository.migrate()

    if role in _ORG_ROLES and repository.organization(arguments.organization) is None:
        _fail(
            f"organization {arguments.organization!r} does not exist; "
            "create it first (see scripts/securityHelper.py)"
        )
    if role in _MODEL_ROLES and role is not None and repository.model(arguments.model) is None:
        _fail(f"model {arguments.model!r} does not exist")

    existing = repository.principal(identifier)
    repository.save_principal(
        PrincipalRecord(
            id=identifier,
            external_identity=username,
            display_name=arguments.display_name or username,
            kind=authz.PrincipalKind(arguments.kind),
        )
    )
    print(f"{'updated' if existing else 'registered'} principal {identifier}")
    print(f"  expects SSO remote-user: {username}")

    if role is None:
        print("  no role given; grant one with --role before the user can do anything")
    elif role in _ORG_ROLES:
        repository.add_organization_membership(arguments.organization, identifier, role)
        print(f"  {role.value} on organization {arguments.organization}")
    else:
        repository.add_model_grant(arguments.model, identifier, role)
        print(f"  {role.value} on model {arguments.model}")

    _print_grants(repository, identifier)


def show(arguments: argparse.Namespace) -> None:
    username = _check_username(arguments.username)
    identifier = principal_id(username, arguments.issuer)

    repository = SQLiteMetadataRepository()
    record = repository.principal(identifier)
    if record is None:
        _fail(
            f"no principal {identifier!r} in {repository.path}; "
            "an unregistered user authenticates but sees nothing"
        )
        return
    print(f"{record.id}")
    print(f"  display name: {record.display_name}")
    print(f"  kind: {record.kind.value}")
    print(f"  expects SSO remote-user: {record.external_identity}")
    _print_grants(repository, identifier)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m helios_core.metadata.users",
        description="Register Helios principals and grant them access.",
        epilog=(
            "examples:\n"
            "  add --username jdoe --role org_admin --organization default\n"
            "  add --username jdoe --role model_consumer --model tpcds\n"
            "  show --username jdoe"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--username", required=True, help="the user's SSO remote-user value")
    common.add_argument(
        "--issuer", default=DEFAULT_ISSUER, help=f"identity issuer (default: {DEFAULT_ISSUER})"
    )

    adder = subcommands.add_parser(
        "add", parents=[common], help="register a principal and optionally grant a role"
    )
    adder.add_argument("--display-name", help="human-readable name (default: the username)")
    adder.add_argument(
        "--kind",
        default=authz.PrincipalKind.HUMAN.value,
        choices=[kind.value for kind in authz.PrincipalKind],
        help="principal kind (default: human)",
    )
    adder.add_argument(
        "--role", help="role to grant; org_admin needs --organization, the rest need --model"
    )
    adder.add_argument("--organization", help="organization id, for an org_admin membership")
    adder.add_argument("--model", help="model id, for a per-model grant")
    adder.set_defaults(handler=add)

    viewer = subcommands.add_parser(
        "show", parents=[common], help="show a principal and its effective grants"
    )
    viewer.set_defaults(handler=show)
    return parser


def main(argv: list[str] | None = None) -> None:
    arguments = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    arguments.handler(arguments)


if __name__ == "__main__":
    main()
