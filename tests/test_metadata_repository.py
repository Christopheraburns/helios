import sqlite3

import pytest

from helios_core import authz
from helios_core.domain import (
    DataSource,
    DataSourceReference,
    Model,
    Organization,
)
from helios_core.metadata import PrincipalRecord, SQLiteMetadataRepository


@pytest.fixture
def repository(tmp_path):
    repo = SQLiteMetadataRepository(tmp_path / "helios.db")
    repo.migrate()
    return repo


def principal(subject: str) -> PrincipalRecord:
    return PrincipalRecord(
        id=f"cloudera-workbench:{subject}",
        external_identity=subject,
        display_name=subject.title(),
    )


def seed_organizations_and_principals(repository):
    repository.save_organization(Organization("acme", "Acme"), "acme")
    repository.save_organization(Organization("other", "Other"), "other")
    repository.save_principal(principal("alice"))
    repository.save_principal(principal("chris"))


def test_migrations_are_versioned_and_idempotent(repository):
    repository.migrate()

    assert repository.schema_version() == 1
    with sqlite3.connect(repository.path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {
        "schema_migrations",
        "organizations",
        "principals",
        "organization_memberships",
        "data_sources",
        "models",
        "model_data_sources",
        "model_memberships",
    } <= tables


def test_operational_metadata_persists_across_repository_instances(
    repository,
):
    seed_organizations_and_principals(repository)
    repository.add_organization_membership(
        "acme", principal("chris").id, authz.Role.ORG_ADMIN
    )
    warehouse = DataSource(
        id="warehouse",
        organization_id="acme",
        name="Production warehouse",
        connector="impala",
        connection_ref="connections/production",
    )
    repository.save_data_source(warehouse)
    model = Model(
        id="customer360",
        organization_id="acme",
        name="Customer 360",
        description="Unified customer semantics",
        data_sources=(
            DataSourceReference(
                "warehouse", ("crm.customers", "sales.orders")
            ),
        ),
        glossary_id="customer-glossary",
        semantic_model_id="customer-semantic",
        ontology_id="customer-ontology",
    )
    stored = repository.save_model(
        model, created_by=principal("alice").id, status="active"
    )
    repository.add_model_grant(
        model.id, principal("alice").id, authz.Role.MODEL_OWNER
    )

    reopened = SQLiteMetadataRepository(repository.path)
    reopened.migrate()
    loaded = reopened.stored_model(model.id)

    assert loaded is not None
    assert loaded.status == "active"
    assert loaded.created_by == principal("alice").id
    assert loaded.model.data_sources == model.data_sources
    assert reopened.data_source("warehouse") == warehouse
    assert reopened.data_sources_for_organization("acme") == [warehouse]
    assert reopened.stored_organization("acme").slug == "acme"
    assert reopened.organization("acme").member_ids == (
        principal("chris").id,
    )
    assert {
        (grant.role, grant.resource.resource_type, grant.resource.resource_id)
        for grant in reopened.grants_for_principal(principal("alice").id)
    } == {(authz.Role.MODEL_OWNER, "model", "customer360")}
    assert {
        (grant.role, grant.resource.resource_type, grant.resource.resource_id)
        for grant in reopened.grants_for_principal(principal("chris").id)
    } == {(authz.Role.ORG_ADMIN, "organization", "acme")}


def test_model_cannot_reference_data_source_from_another_organization(
    repository,
):
    seed_organizations_and_principals(repository)
    repository.save_data_source(
        DataSource(
            id="other-warehouse",
            organization_id="other",
            name="Other warehouse",
            connector="hive",
            connection_ref="connections/other",
        )
    )
    model = Model(
        id="invalid",
        organization_id="acme",
        name="Invalid",
        data_sources=(DataSourceReference("other-warehouse"),),
    )

    with pytest.raises(ValueError, match="another organization"):
        repository.save_model(model, created_by=principal("alice").id)

    assert repository.model("invalid") is None


def test_resources_cannot_be_moved_between_organizations(repository):
    seed_organizations_and_principals(repository)
    repository.save_data_source(
        DataSource(
            "warehouse",
            "acme",
            "Warehouse",
            "impala",
            "connections/acme",
        )
    )

    with pytest.raises(ValueError, match="move"):
        repository.save_data_source(
            DataSource(
                "warehouse",
                "other",
                "Warehouse",
                "impala",
                "connections/other",
            )
        )


def test_repository_stores_references_not_credentials(repository):
    with sqlite3.connect(repository.path) as connection:
        data_source_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(data_sources)")
        }
        principal_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(principals)")
        }

    assert "connection_ref" in data_source_columns
    assert {"password", "secret", "token"}.isdisjoint(data_source_columns)
    assert {"password", "secret", "token"}.isdisjoint(principal_columns)
