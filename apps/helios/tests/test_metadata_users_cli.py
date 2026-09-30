"""The user-onboarding CLI: python -m helios_core.metadata.users.

Adding a user is repository-level only -- no REST, no UI -- so this CLI is the
supported path and its validation is the only thing standing between an operator
and a silently broken account.
"""

import pytest

from helios_core import authz
from helios_core.domain import DataSource, DataSourceReference, Model, Organization
from helios_core.metadata import PrincipalRecord, SQLiteMetadataRepository, users


@pytest.fixture
def repository(tmp_path, monkeypatch):
    """An isolated metadata database seeded with one organization and one model."""
    monkeypatch.setenv("HELIOS_METADATA_DB", str(tmp_path / "helios.db"))
    repo = SQLiteMetadataRepository()
    repo.migrate()
    repo.save_organization(Organization("default", "Default Organization"), slug="default")
    # save_model's created_by is a foreign key onto principals.
    repo.save_principal(
        PrincipalRecord(
            id="cloudera-workbench:seed", external_identity="seed", display_name="seed"
        )
    )
    repo.save_data_source(
        DataSource(
            id="ds1",
            organization_id="default",
            name="DS",
            connector="impala",
            connection_ref="env:IMPALA_HOST",
        )
    )
    repo.save_model(
        Model("tpcds", "default", "TPC-DS", (DataSourceReference("ds1"),)),
        created_by="cloudera-workbench:seed",
    )
    return repo


def run(*argv):
    users.main(list(argv))


# --- identity -------------------------------------------------------------


def test_principal_id_matches_what_the_console_derives_from_sso():
    assert users.principal_id("jdoe") == "cloudera-workbench:jdoe"


def test_issuer_can_be_overridden():
    assert users.principal_id("jdoe", "okta") == "okta:jdoe"


# --- add ------------------------------------------------------------------


def test_add_registers_a_principal(repository):
    run("add", "--username", "jdoe", "--display-name", "J Doe")

    record = repository.principal("cloudera-workbench:jdoe")
    assert record is not None
    assert record.external_identity == "jdoe"
    assert record.display_name == "J Doe"
    assert record.kind is authz.PrincipalKind.HUMAN


def test_display_name_defaults_to_the_username(repository):
    run("add", "--username", "jdoe")
    assert repository.principal("cloudera-workbench:jdoe").display_name == "jdoe"


def test_kind_is_respected(repository):
    run("add", "--username", "crawler", "--kind", "agent")
    assert repository.principal("cloudera-workbench:crawler").kind is authz.PrincipalKind.AGENT


def test_registering_without_a_role_leaves_the_user_with_no_grants(repository):
    run("add", "--username", "jdoe")
    assert repository.grants_for_principal("cloudera-workbench:jdoe") == []


def test_org_admin_membership_is_granted(repository):
    run("add", "--username", "jdoe", "--role", "org_admin", "--organization", "default")

    grants = repository.grants_for_principal("cloudera-workbench:jdoe")
    assert [g.role for g in grants] == [authz.Role.ORG_ADMIN]
    assert grants[0].resource.resource_id == "default"


def test_model_grant_is_granted(repository):
    run("add", "--username", "analyst", "--role", "model_consumer", "--model", "tpcds")

    grants = repository.grants_for_principal("cloudera-workbench:analyst")
    assert [g.role for g in grants] == [authz.Role.MODEL_CONSUMER]
    assert grants[0].resource.resource_id == "tpcds"


def test_role_is_case_insensitive(repository):
    run("add", "--username", "jdoe", "--role", "ORG_ADMIN", "--organization", "default")
    assert repository.grants_for_principal("cloudera-workbench:jdoe")


def test_rerunning_does_not_duplicate_grants(repository):
    for _ in range(3):
        run("add", "--username", "analyst", "--role", "model_consumer", "--model", "tpcds")
    assert len(repository.grants_for_principal("cloudera-workbench:analyst")) == 1


def test_rerunning_updates_the_display_name(repository):
    run("add", "--username", "jdoe", "--display-name", "Old")
    run("add", "--username", "jdoe", "--display-name", "New")
    assert repository.principal("cloudera-workbench:jdoe").display_name == "New"


# --- validation -----------------------------------------------------------


@pytest.mark.parametrize("username", ["", "   ", "has:colon", "has space", "tab\there"])
def test_malformed_usernames_are_rejected(repository, username):
    with pytest.raises(SystemExit):
        run("add", "--username", username, "--role", "org_admin", "--organization", "default")


def test_unknown_role_is_rejected_and_lists_the_valid_ones(repository):
    with pytest.raises(SystemExit) as exit_info:
        run("add", "--username", "jdoe", "--role", "wizard")
    assert "org_admin" in str(exit_info.value)


def test_org_admin_requires_an_organization(repository):
    with pytest.raises(SystemExit, match="--organization"):
        run("add", "--username", "jdoe", "--role", "org_admin")


def test_model_roles_require_a_model(repository):
    with pytest.raises(SystemExit, match="--model"):
        run("add", "--username", "jdoe", "--role", "model_consumer")


def test_unknown_organization_is_rejected(repository):
    with pytest.raises(SystemExit, match="does not exist"):
        run("add", "--username", "jdoe", "--role", "org_admin", "--organization", "ghost")


def test_unknown_model_is_rejected(repository):
    with pytest.raises(SystemExit, match="does not exist"):
        run("add", "--username", "jdoe", "--role", "model_consumer", "--model", "ghost")


def test_a_rejected_command_creates_no_principal(repository):
    """Validation must precede the write, or a failed run leaves a half-made account."""
    with pytest.raises(SystemExit):
        run("add", "--username", "jdoe", "--role", "org_admin", "--organization", "ghost")
    assert repository.principal("cloudera-workbench:jdoe") is None


def test_org_admin_cannot_be_smuggled_in_as_a_model_grant(repository):
    """The repository forbids it; the CLI must not find a way around that."""
    with pytest.raises(SystemExit, match="--organization"):
        run("add", "--username", "jdoe", "--role", "org_admin", "--model", "tpcds")


# --- show -----------------------------------------------------------------


def test_show_reports_the_principal_and_its_grants(repository, capsys):
    run("add", "--username", "jdoe", "--role", "org_admin", "--organization", "default")
    capsys.readouterr()

    run("show", "--username", "jdoe")
    output = capsys.readouterr().out
    assert "cloudera-workbench:jdoe" in output
    assert "org_admin on organization default" in output
    assert "expects SSO remote-user: jdoe" in output


def test_show_explains_an_unregistered_user(repository):
    with pytest.raises(SystemExit, match="authenticates but sees nothing"):
        run("show", "--username", "nobody")


def test_show_warns_when_a_principal_has_no_grants(repository, capsys):
    run("add", "--username", "jdoe")
    capsys.readouterr()

    run("show", "--username", "jdoe")
    assert "empty console" in capsys.readouterr().out
