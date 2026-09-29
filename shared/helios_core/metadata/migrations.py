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
    (
        3,
        """
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY,
            model_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
            principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX conversations_owner_model_idx
            ON conversations (principal_id, model_id, updated_at DESC);

        CREATE TABLE conversation_messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL
                REFERENCES conversations(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (conversation_id, position)
        );

        CREATE INDEX conversation_messages_conversation_idx
            ON conversation_messages (conversation_id, position);
        """,
    ),
    (
        4,
        """
        CREATE TABLE audit_events (
            id TEXT PRIMARY KEY,
            occurred_at TEXT NOT NULL,
            request_id TEXT,
            session_id TEXT,
            principal_id TEXT,
            organization_id TEXT,
            model_id TEXT,
            component TEXT NOT NULL,
            event_type TEXT NOT NULL,
            action TEXT NOT NULL,
            resource_type TEXT,
            resource_id TEXT,
            outcome TEXT NOT NULL,
            severity TEXT NOT NULL,
            http_status INTEGER,
            duration_ms REAL,
            summary TEXT NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX audit_events_principal_session_time_idx
            ON audit_events (
                principal_id, session_id, occurred_at DESC, id DESC
            );
        CREATE INDEX audit_events_organization_time_idx
            ON audit_events (organization_id, occurred_at DESC, id DESC);
        CREATE INDEX audit_events_model_time_idx
            ON audit_events (model_id, occurred_at DESC, id DESC);
        CREATE INDEX audit_events_component_time_idx
            ON audit_events (component, occurred_at DESC, id DESC);
        """,
    ),
    (
        5,
        """
        ALTER TABLE conversations
            ADD COLUMN archived_at TEXT;

        CREATE INDEX conversations_owner_model_active_idx
            ON conversations (
                principal_id, model_id, archived_at, updated_at DESC
            );

        CREATE TABLE conversation_turns (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL
                REFERENCES conversations(id) ON DELETE CASCADE,
            user_message_id TEXT NOT NULL
                REFERENCES conversation_messages(id) ON DELETE CASCADE,
            assistant_message_id TEXT NOT NULL UNIQUE
                REFERENCES conversation_messages(id) ON DELETE CASCADE,
            request_id TEXT,
            tool_trace_json TEXT NOT NULL DEFAULT '[]',
            query_result_json TEXT,
            provenance_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE (conversation_id, user_message_id, assistant_message_id)
        );

        CREATE INDEX conversation_turns_conversation_idx
            ON conversation_turns (conversation_id, created_at, id);
        """,
    ),
    (
        6,
        """
        ALTER TABLE conversation_turns
            ADD COLUMN trace_run_id TEXT;

        CREATE TABLE agent_trace_runs (
            id TEXT PRIMARY KEY,
            request_id TEXT,
            conversation_id TEXT
                REFERENCES conversations(id) ON DELETE SET NULL,
            principal_id TEXT NOT NULL REFERENCES principals(id),
            organization_id TEXT NOT NULL
                REFERENCES organizations(id) ON DELETE CASCADE,
            model_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
            purpose TEXT NOT NULL CHECK (
                purpose IN ('conversation', 'evaluation')
            ),
            question_id TEXT,
            question TEXT NOT NULL,
            llm_provider TEXT NOT NULL,
            llm_model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('running', 'completed', 'failed', 'cancelled')
            ),
            termination_reason TEXT,
            answer TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            duration_ms REAL,
            tokens_in INTEGER NOT NULL DEFAULT 0,
            tokens_out INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX agent_trace_runs_owner_model_time_idx
            ON agent_trace_runs (
                principal_id, model_id, started_at DESC, id DESC
            );
        CREATE INDEX agent_trace_runs_org_time_idx
            ON agent_trace_runs (
                organization_id, started_at DESC, id DESC
            );

        CREATE TABLE agent_trace_spans (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL
                REFERENCES agent_trace_runs(id) ON DELETE CASCADE,
            parent_span_id TEXT,
            sequence INTEGER NOT NULL,
            component TEXT NOT NULL,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            latency_ms REAL,
            input_json TEXT NOT NULL DEFAULT '{}',
            output_json TEXT NOT NULL DEFAULT '{}',
            attributes_json TEXT NOT NULL DEFAULT '{}',
            error TEXT,
            UNIQUE (run_id, sequence, component)
        );

        CREATE INDEX agent_trace_spans_run_sequence_idx
            ON agent_trace_spans (run_id, sequence, component, id);

        CREATE TABLE evaluation_runs (
            id TEXT PRIMARY KEY,
            principal_id TEXT NOT NULL REFERENCES principals(id),
            organization_id TEXT NOT NULL
                REFERENCES organizations(id) ON DELETE CASCADE,
            model_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
            suite_id TEXT NOT NULL,
            suite_version TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN (
                    'queued', 'running', 'completed', 'failed', 'cancelled'
                )
            ),
            repetitions INTEGER NOT NULL,
            baseline_provider TEXT NOT NULL,
            baseline_model TEXT NOT NULL,
            candidate_provider TEXT NOT NULL,
            candidate_model TEXT NOT NULL,
            max_tool_rounds INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            error TEXT,
            metrics_json TEXT NOT NULL DEFAULT '{}',
            cancel_requested INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX evaluation_runs_org_model_time_idx
            ON evaluation_runs (
                organization_id, model_id, created_at DESC, id DESC
            );

        CREATE TABLE evaluation_results (
            id TEXT PRIMARY KEY,
            evaluation_run_id TEXT NOT NULL
                REFERENCES evaluation_runs(id) ON DELETE CASCADE,
            question_id TEXT NOT NULL,
            variant TEXT NOT NULL CHECK (
                variant IN ('baseline', 'candidate')
            ),
            repetition INTEGER NOT NULL,
            trace_run_id TEXT NOT NULL
                REFERENCES agent_trace_runs(id) ON DELETE CASCADE,
            accurate INTEGER NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0,
            metrics_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE (
                evaluation_run_id, question_id, variant, repetition
            )
        );

        CREATE INDEX evaluation_results_run_question_idx
            ON evaluation_results (
                evaluation_run_id, question_id, variant, repetition
            );
        """,
    ),
    (
        7,
        """
        CREATE TABLE semantic_artifact_revisions (
            id TEXT PRIMARY KEY,
            model_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
            sha256 TEXT NOT NULL,
            artifact_path TEXT NOT NULL,
            ossie_version TEXT,
            discovery_run_id TEXT,
            published_at TEXT NOT NULL,
            published_by TEXT,
            size_bytes INTEGER NOT NULL,
            UNIQUE (model_id, sha256)
        );

        CREATE INDEX semantic_artifact_revisions_model_time_idx
            ON semantic_artifact_revisions (
                model_id, published_at DESC, id DESC
            );

        ALTER TABLE agent_trace_runs
            ADD COLUMN semantic_revision_id TEXT
                REFERENCES semantic_artifact_revisions(id);

        CREATE INDEX agent_trace_runs_semantic_revision_idx
            ON agent_trace_runs (semantic_revision_id);
        """,
    ),
)
