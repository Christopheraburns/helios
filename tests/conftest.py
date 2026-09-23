from dataclasses import dataclass

import pytest

from helios_core import authz
from helios_core.artifacts import ArtifactStore
from helios_core.domain import (
    DataSource,
    DataSourceReference,
    Model,
    Organization,
)
from helios_core.metadata import PrincipalRecord, SQLiteMetadataRepository


@dataclass(frozen=True)
class PersistentAuthorizationStack:
    repository: SQLiteMetadataRepository
    artifacts: ArtifactStore
    acme: Organization
    other: Organization
    customer: Model
    finance: Model
    other_model: Model


def _principal(subject: str) -> PrincipalRecord:
    return PrincipalRecord(
        id=f"cloudera-workbench:{subject}",
        external_identity=subject,
        display_name=subject.replace("-", " ").title(),
    )


def _ossie(
    name: str,
    dataset: str,
    field: str,
    metric: str,
) -> dict:
    return {
        "version": "0.2.0.dev0",
        "name": name,
        "description": f"Semantic model for {name}",
        "datasets": [
            {
                "name": dataset,
                "source": f"warehouse.{dataset}",
                "fields": [
                    {
                        "name": field,
                        "label": field.replace("_", " ").title(),
                        "datatype": "String",
                        "expression": {
                            "dialects": [
                                {
                                    "dialect": "ANSI_SQL",
                                    "expression": field,
                                }
                            ]
                        },
                        "dimension": {"is_time": False},
                    }
                ],
            }
        ],
        "relationships": [],
        "metrics": [
            {
                "name": metric,
                "description": f"{metric} metric",
                "datatype": "Decimal",
                "expression": {
                    "dialects": [
                        {
                            "dialect": "ANSI_SQL",
                            "expression": f"COUNT({dataset}.{field})",
                        }
                    ]
                },
            }
        ],
    }


@pytest.fixture
def persistent_auth_stack(tmp_path) -> PersistentAuthorizationStack:
    repository = SQLiteMetadataRepository(tmp_path / "metadata.db")
    repository.migrate()

    acme = Organization("acme", "Acme")
    other = Organization("other", "Other")
    repository.save_organization(acme, "acme")
    repository.save_organization(other, "other")

    for subject in (
        "admin",
        "owner",
        "viewer",
        "editor",
        "other-owner",
        "outsider",
    ):
        repository.save_principal(_principal(subject))

    shared = DataSource(
        "shared-warehouse",
        acme.id,
        "Shared warehouse",
        "impala",
        "connections/shared",
    )
    other_source = DataSource(
        "other-warehouse",
        other.id,
        "Other warehouse",
        "hive",
        "connections/other",
    )
    repository.save_data_source(shared)
    repository.save_data_source(other_source)

    customer = Model(
        "customer360",
        acme.id,
        "Customer 360",
        (
            DataSourceReference(
                shared.id, ("crm.customers", "sales.orders")
            ),
        ),
        description="Customer semantics",
        version_ids=("customer-v1",),
        discovery_run_ids=("customer-run",),
        glossary_id="customer-glossary",
        semantic_model_id="customer-semantic",
        ontology_id="customer-ontology",
    )
    finance = Model(
        "finance",
        acme.id,
        "Finance",
        (DataSourceReference(shared.id, ("finance.ledger",)),),
        description="Finance semantics",
        version_ids=("finance-v1",),
        glossary_id="finance-glossary",
        semantic_model_id="finance-semantic",
        ontology_id="finance-ontology",
    )
    other_model = Model(
        "other-model",
        other.id,
        "Other model",
        (DataSourceReference(other_source.id),),
        description="Other organization semantics",
    )
    repository.save_model(customer, _principal("owner").id, "active")
    repository.save_model(finance, _principal("editor").id, "active")
    repository.save_model(other_model, _principal("other-owner").id, "active")

    repository.add_organization_membership(
        acme.id, _principal("admin").id, authz.Role.ORG_ADMIN
    )
    repository.add_model_grant(
        customer.id, _principal("owner").id, authz.Role.MODEL_OWNER
    )
    repository.add_model_grant(
        customer.id, _principal("viewer").id, authz.Role.MODEL_VIEWER
    )
    repository.add_model_grant(
        finance.id, _principal("editor").id, authz.Role.MODEL_EDITOR
    )
    repository.add_model_grant(
        other_model.id,
        _principal("other-owner").id,
        authz.Role.MODEL_OWNER,
    )

    artifacts = ArtifactStore(tmp_path / "artifact-root")
    customer_doc = _ossie(
        "Customer 360", "customers", "customer_id", "customer_count"
    )
    finance_doc = _ossie(
        "Finance", "ledger", "account_id", "account_count"
    )
    other_doc = _ossie(
        "Other", "restricted", "restricted_id", "restricted_count"
    )
    artifacts.write_published_ossie(
        customer.id,
        customer_doc,
        "name: Customer 360\n",
        {"run_id": "customer-run"},
    )
    artifacts.write_published_ossie(
        finance.id,
        finance_doc,
        "name: Finance\n",
        {"run_id": "finance-run"},
    )
    artifacts.write_published_ossie(
        other_model.id,
        other_doc,
        "name: Other\n",
        {"run_id": "other-run"},
    )

    return PersistentAuthorizationStack(
        repository,
        artifacts,
        acme,
        other,
        customer,
        finance,
        other_model,
    )
