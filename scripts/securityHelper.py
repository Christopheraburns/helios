from helios_core import authz
from helios_core.domain import (
    DataSource,
    DataSourceReference,
    Model,
    Organization,
)
from helios_core.metadata import PrincipalRecord, SQLiteMetadataRepository

username = "cburns"
principal_id = f"cloudera-workbench:{username}"
organization_id = "default"
data_source_id = "tpcds-impala"
model_id = "tpcds"

repo = SQLiteMetadataRepository()
repo.migrate()

repo.save_organization(
    Organization(organization_id, "Default Organization"),
    slug="default",
)

repo.save_principal(
    PrincipalRecord(
        id=principal_id,
        external_identity=username,
        display_name=username,
    )
)

repo.add_organization_membership(
    organization_id,
    principal_id,
    authz.Role.ORG_ADMIN,
)

repo.save_data_source(
    DataSource(
        id=data_source_id,
        organization_id=organization_id,
        name="TPC-DS Impala",
        connector="impala",
        connection_ref="env:IMPALA_HOST",
    )
)

repo.save_model(
    Model(
        id=model_id,
        organization_id=organization_id,
        name="TPC-DS",
        description="TPC-DS semantic model",
        data_sources=(DataSourceReference(data_source_id),),
        discovery_run_ids=("20260922T000009Z",),
        semantic_model_id=model_id,
    ),
    created_by=principal_id,
    status="published",
)

stored_model = repo.stored_model(model_id)
if stored_model is None or stored_model.model.organization_id != organization_id:
    raise RuntimeError("TPC-DS model registration could not be verified")

print(
    f"Registered model {model_id!r} in organization {organization_id!r} "
    f"for principal {principal_id!r}."
)
