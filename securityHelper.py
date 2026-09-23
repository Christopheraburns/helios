from helios_core import authz
from helios_core.domain import Organization
from helios_core.metadata import PrincipalRecord, SQLiteMetadataRepository

username = "cburns"
principal_id = f"cloudera-workbench:{username}"
organization_id = "default"

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