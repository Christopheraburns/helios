"""Ordered SQLite schema migrations for operational metadata.

Append migrations; never edit an applied migration after release.
"""

MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE organizations (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE
        );

        CREATE TABLE principals (
            id TEXT PRIMARY KEY,
            external_identity TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('human', 'service', 'agent'))
        );

        CREATE TABLE organization_memberships (
            organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK (role IN ('org_admin')),
            PRIMARY KEY (organization_id, principal_id, role)
        );

        CREATE TABLE data_sources (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            connector TEXT NOT NULL,
            connection_ref TEXT NOT NULL,
            UNIQUE (organization_id, name)
        );

        CREATE INDEX data_sources_organization_idx
            ON data_sources (organization_id);

        CREATE TABLE models (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft',
            created_by TEXT NOT NULL REFERENCES principals(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            glossary_id TEXT,
            semantic_model_id TEXT,
            ontology_id TEXT,
            UNIQUE (organization_id, name)
        );

        CREATE INDEX models_organization_idx ON models (organization_id);

        CREATE TABLE model_data_sources (
            model_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
            data_source_id TEXT NOT NULL REFERENCES data_sources(id) ON DELETE RESTRICT,
            selected_assets_json TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (model_id, data_source_id)
        );

        CREATE TRIGGER model_data_sources_same_organization
        BEFORE INSERT ON model_data_sources
        BEGIN
            SELECT CASE WHEN (
                SELECT organization_id FROM models WHERE id = NEW.model_id
            ) != (
                SELECT organization_id FROM data_sources WHERE id = NEW.data_source_id
            ) THEN RAISE(
                ABORT,
                'model and data source must belong to the same organization'
            ) END;
        END;

        CREATE TABLE model_memberships (
            model_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
            principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK (
                role IN (
                    'model_owner',
                    'model_editor',
                    'model_viewer',
                    'model_consumer'
                )
            ),
            PRIMARY KEY (model_id, principal_id, role)
        );

        CREATE INDEX model_memberships_principal_idx
            ON model_memberships (principal_id);
        """,
    ),
    (
        2,
        """
        ALTER TABLE models
            ADD COLUMN version_ids_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE models
            ADD COLUMN discovery_run_ids_json TEXT NOT NULL DEFAULT '[]';
        """,
    ),
)
