import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from helios_core import authz
from helios_core.domain import (
    DataSource,
    DataSourceReference,
    Model,
    Organization,
)
from helios_core.metadata import (
    EvaluationResult,
    EvaluationRun,
    PrincipalRecord,
    SemanticRevision,
    SQLiteMetadataRepository,
    TraceRun,
    TraceSpan,
)
from helios_core.metadata.repair import rebuild_index, recover_database
from helios_core.tracing import TraceRecorder


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

    assert repository.schema_version() == 8
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
        "agent_trace_runs",
        "agent_trace_spans",
        "semantic_artifact_revisions",
        "evaluation_runs",
        "evaluation_results",
    } <= tables


def test_trace_and_evaluation_records_round_trip(repository):
    repository.save_organization(Organization("acme", "Acme"), "acme")
    repository.save_principal(principal("alice"))
    repository.save_data_source(
        DataSource("warehouse", "acme", "Warehouse", "impala", "connection")
    )
    repository.save_model(
        Model(
            "customer360",
            "acme",
            "Customer 360",
            (DataSourceReference("warehouse"),),
        ),
        created_by=principal("alice").id,
    )
    now = datetime.now(UTC)
    revision = SemanticRevision(
        id="a" * 64,
        model_id="customer360",
        sha256="a" * 64,
        artifact_path=(
            "models/customer360/revisions/"
            + ("a" * 64)
            + "/semantic.ossie.yaml"
        ),
        ossie_version="0.2.0",
        discovery_run_id="discovery-1",
        published_at=now,
        published_by=principal("alice").id,
        size_bytes=100,
    )
    repository.save_semantic_revision(revision)
    trace = TraceRun(
        id="trace-1",
        principal_id="cloudera-workbench:alice",
        organization_id="acme",
        model_id="customer360",
        purpose="evaluation",
        question="Revenue by channel?",
        question_id="Q3",
        llm_provider="mistral",
        llm_model="mistral-small-latest",
        prompt_version="talk-v1",
        status="running",
        started_at=now,
        semantic_revision_id=revision.id,
    )
    repository.create_trace_run(trace)
    repository.append_trace_span(
        TraceSpan(
            id="span-1",
            run_id=trace.id,
            sequence=1,
            component="agent",
            kind="tool",
            name="query",
            status="ok",
            started_at=now,
            input={"metric": "revenue"},
            output={"rows": 2},
        )
    )
    finished = repository.update_trace_run(
        trace.id,
        status="completed",
        termination_reason="final_answer",
        answer="Two channels.",
        completed_at=now,
        duration_ms=25,
        tokens_in=100,
        tokens_out=12,
    )

    assert finished.answer == "Two channels."
    assert repository.trace_run(trace.id) == finished
    assert repository.semantic_revision(revision.id) == revision
    assert repository.latest_semantic_revision("customer360") == revision
    assert repository.trace_spans(trace.id)[0].input == {"metric": "revenue"}
    trace_runs, total = repository.trace_runs(
        model_id="customer360",
        principal_id="cloudera-workbench:alice",
    )
    assert trace_runs == [finished]
    assert total == 1

    evaluation = EvaluationRun(
        id="eval-1",
        principal_id="cloudera-workbench:alice",
        organization_id="acme",
        model_id="customer360",
        suite_id="tpcds",
        suite_version="v1",
        status="running",
        repetitions=1,
        baseline_provider="anthropic",
        baseline_model="haiku",
        candidate_provider="mistral",
        candidate_model="small",
        max_tool_rounds=6,
        created_at=now,
    )
    repository.create_evaluation_run(evaluation)
    result = EvaluationResult(
        id="result-1",
        evaluation_run_id=evaluation.id,
        question_id="Q3",
        variant="candidate",
        repetition=1,
        trace_run_id=trace.id,
        accurate=True,
        completed=True,
        metrics={"tool_calls": 2},
    )
    repository.append_evaluation_result(result)

    assert repository.evaluation_results(evaluation.id) == [result]
    cancelling = repository.update_evaluation_run(
        evaluation.id,
        status="running",
        cancel_requested=True,
    )
    assert cancelling.cancel_requested is True
    assert repository.fail_interrupted_evaluations() == 1
    interrupted = repository.evaluation_run(evaluation.id)
    assert interrupted is not None
    assert interrupted.status == "failed"
    assert interrupted.error == (
        "The API restarted before this evaluation completed."
    )


def test_trace_recorder_redacts_secrets_and_bounds_payloads(repository):
    repository.save_organization(Organization("acme", "Acme"), "acme")
    repository.save_principal(principal("alice"))
    repository.save_data_source(
        DataSource("warehouse", "acme", "Warehouse", "impala", "connection")
    )
    repository.save_model(
        Model(
            "customer360",
            "acme",
            "Customer 360",
            (DataSourceReference("warehouse"),),
        ),
        created_by=principal("alice").id,
    )
    recorder = TraceRecorder.start(
        repository,
        principal_id=principal("alice").id,
        organization_id="acme",
        model_id="customer360",
        question="Count customers",
        provider="anthropic",
        llm_model="haiku",
        request_id="request-1",
    )
    now = datetime.now(UTC)
    recorder.span(
        component="agent",
        kind="llm",
        name="anthropic.chat",
        status="success",
        started_at=now,
        completed_at=now,
        input={
            "authorization": "Bearer secret",
            "nested": {"api_key": "secret"},
            "prompt": "x" * 100_001,
        },
    )

    stored = repository.trace_spans(recorder.run.id)[0]
    assert stored.input["authorization"] == "[redacted]"
    assert stored.input["nested"]["api_key"] == "[redacted]"
    assert stored.input["prompt"].endswith("…")


def test_sqlite_defaults_to_rollback_journal_and_reports_integrity(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("HELIOS_SQLITE_JOURNAL_MODE", raising=False)
    repository = SQLiteMetadataRepository(tmp_path / "rollback.db")
    repository.migrate()
    with sqlite3.connect(repository.path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    assert journal_mode == "delete"
    assert repository.integrity_check() == ("ok",)
    assert repository.integrity_check(thorough=True) == ("ok",)
    assert repository.is_healthy()


def test_sqlite_rejects_unknown_journal_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("HELIOS_SQLITE_JOURNAL_MODE", "unsafe-mode")
    repository = SQLiteMetadataRepository(tmp_path / "invalid.db")

    with pytest.raises(ValueError, match="HELIOS_SQLITE_JOURNAL_MODE"):
        repository.migrate()


def test_backup_first_index_rebuild_and_logical_recovery(repository):
    seed_organizations_and_principals(repository)
    backup = rebuild_index(
        Path(repository.path),
        "audit_events_principal_session_time_idx",
    )

    assert (backup / "helios.db").is_file()
    assert repository.integrity_check(thorough=True) == ("ok",)

    recovery_backup = recover_database(Path(repository.path))
    recovered = SQLiteMetadataRepository(repository.path)
    assert (recovery_backup / "helios.db").is_file()
    assert recovered.organization("acme") is not None
    assert recovered.principal(principal("alice").id) is not None
    assert recovered.integrity_check(thorough=True) == ("ok",)


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
    assert loaded.model.version_ids == model.version_ids
    assert loaded.model.discovery_run_ids == model.discovery_run_ids
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


def test_data_sources_keep_crawl_scope_and_can_be_deleted_when_unused(repository):
    """DS-2: crawl configuration round-trips; a model's data source can't be deleted."""
    seed_organizations_and_principals(repository)
    source = DataSource(
        id="corpus",
        organization_id="acme",
        name="Helios-DS development corpus",
        connector="helios_ds",
        connection_ref="S3 Object Store",
        description="The crawler's development corpus",
        scope={"dataset_id": "1ca99f86"},
        crawl={"enabled": True, "settings_version": None, "schedule": "manual"},
        updated_at="2026-10-02T00:00:00+00:00",
        updated_by="cloudera-workbench:alice",
    )
    repository.save_data_source(source)
    assert repository.data_source("corpus") == source
    assert repository.data_sources_for_organization("acme")[0].scope == {"dataset_id": "1ca99f86"}

    repository.save_data_source(
        DataSource(id="warehouse", organization_id="acme", name="Warehouse",
                   connector="impala", connection_ref="impala")
    )
    repository.save_model(
        Model(id="m", organization_id="acme", name="M",
              data_sources=(DataSourceReference("warehouse"),)),
        created_by=principal("alice").id,
    )
    with pytest.raises(ValueError, match="used by a model"):
        repository.delete_data_source("warehouse")
    assert repository.delete_data_source("corpus") is True
    assert repository.data_source("corpus") is None
    assert repository.delete_data_source("corpus") is False
