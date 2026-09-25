from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from apps.console.main import app
from helios_core import audit
from helios_core.metadata import AuditEvent, SQLiteMetadataRepository


def event(
    event_id: str,
    principal_id: str,
    session_id: str,
    organization_id: str = "acme",
    *,
    occurred_at: datetime | None = None,
) -> AuditEvent:
    return AuditEvent(
        id=event_id,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        request_id=f"request-{event_id}",
        session_id=session_id,
        principal_id=principal_id,
        organization_id=organization_id,
        model_id="customer360",
        component="api",
        event_type="http.request",
        action="GET /api/v1/models/{model_id}",
        resource_type="model",
        resource_id="customer360",
        outcome="success",
        severity="info",
        http_status=200,
        duration_ms=12.5,
        summary="Model details viewed",
        details={"method": "GET"},
    )


def test_audit_repository_filters_sessions_and_retention(
    persistent_auth_stack,
):
    repository = persistent_auth_stack.repository
    old = datetime.now(timezone.utc) - timedelta(days=60)
    repository.append_audit_event(
        event(
            "old",
            "cloudera-workbench:owner",
            "session-owner",
            occurred_at=old,
        )
    )
    repository.append_audit_event(
        event(
            "current",
            "cloudera-workbench:owner",
            "session-owner",
        )
    )
    repository.append_audit_event(
        event(
            "viewer",
            "cloudera-workbench:viewer",
            "session-viewer",
        )
    )

    events, total = repository.audit_events(
        principal_id="cloudera-workbench:owner",
        organization_id="acme",
        offset=0,
        limit=10,
    )
    sessions = repository.audit_sessions(
        principal_id="cloudera-workbench:owner"
    )
    deleted = repository.purge_audit_events(
        datetime.now(timezone.utc) - timedelta(days=30)
    )

    assert total == 2
    assert {item.id for item in events} == {"old", "current"}
    assert sessions[0].session_id == "session-owner"
    assert sessions[0].event_count == 2
    assert deleted == 1
    assert repository.audit_event("old") is None


def test_audit_redaction_removes_sensitive_values():
    redacted = audit.redact_details(
        {
            "Authorization": "Bearer secret",
            "MISTRAL_API_KEY": "secret",
            "nested": {
                "password": "secret",
                "safe": "visible",
            },
            "rows": [["private"]],
        }
    )

    assert redacted["Authorization"] == "[redacted]"
    assert redacted["MISTRAL_API_KEY"] == "[redacted]"
    assert redacted["nested"]["password"] == "[redacted]"
    assert redacted["nested"]["safe"] == "visible"
    assert redacted["rows"] == "[redacted]"


def test_exception_diagnostics_redact_known_secrets_and_excluded_sql(
    monkeypatch,
):
    monkeypatch.setenv("WORKLOAD_PASSWORD", "private-password")
    sql = "SELECT private_column FROM private_table"
    try:
        raise RuntimeError(
            f"warehouse failed for private-password while running {sql}"
        )
    except RuntimeError as exc:
        diagnostics = audit.exception_diagnostics(
            exc,
            stage="impala_query",
            excluded_values=(sql,),
        )

    rendered = str(diagnostics)
    assert diagnostics["stage"] == "impala_query"
    assert diagnostics["exception_chain"][0]["exception_type"] == "RuntimeError"
    assert "private-password" not in rendered
    assert sql not in rendered
    assert rendered.count("[redacted]") == 2


@pytest.fixture
def audit_client(persistent_auth_stack):
    previous = dict(app.state._state)
    app.state._state.pop("resource_store", None)
    app.state._state.pop("authorization_policy", None)
    app.state.metadata_repository = persistent_auth_stack.repository
    try:
        with TestClient(app) as client:
            yield client, persistent_auth_stack.repository
    finally:
        app.state._state.clear()
        app.state._state.update(previous)


def headers(subject: str, session_id: str = "browser-session") -> dict:
    return {
        "x-forwarded-user": subject,
        "x-helios-session-id": session_id,
    }


def test_audit_api_isolates_own_events_and_hides_foreign_detail(audit_client):
    client, repository = audit_client
    owner_event = event(
        "owner-event", "cloudera-workbench:owner", "owner-session"
    )
    repository.append_audit_event(
        AuditEvent(
            **{
                **owner_event.__dict__,
                "details": {
                    "method": "GET",
                    "diagnostics": {
                        "stage": "impala_query",
                        "exception_chain": [
                            {
                                "exception_type": "RuntimeError",
                                "description": "restricted detail",
                            }
                        ],
                    },
                },
            }
        )
    )
    repository.append_audit_event(
        event("viewer-event", "cloudera-workbench:viewer", "viewer-session")
    )

    own = client.get(
        "/api/v1/audit/events?organization_id=acme",
        headers=headers("owner"),
    )
    foreign_filter = client.get(
        "/api/v1/audit/events"
        "?organization_id=acme"
        "&principal_id=cloudera-workbench%3Aviewer",
        headers=headers("owner"),
    )
    foreign_detail = client.get(
        "/api/v1/audit/events/viewer-event",
        headers=headers("owner"),
    )
    own_detail = client.get(
        "/api/v1/audit/events/owner-event",
        headers=headers("owner"),
    )

    assert own.status_code == 200
    assert {item["id"] for item in own.json()["items"]} == {"owner-event"}
    assert own.json()["items"][0]["diagnostics"] is None
    assert "diagnostics" not in own.json()["items"][0]["details"]
    assert own_detail.status_code == 200
    assert own_detail.json()["diagnostics"] is None
    assert "diagnostics" not in own_detail.json()["details"]
    assert foreign_filter.status_code == 403
    assert foreign_detail.status_code == 404


def test_org_admin_can_read_only_their_organization_activity(audit_client):
    client, repository = audit_client
    repository.append_audit_event(
        event("owner-event", "cloudera-workbench:owner", "owner-session")
    )
    repository.append_audit_event(
        event("viewer-event", "cloudera-workbench:viewer", "viewer-session")
    )
    repository.append_audit_event(
        event(
            "other-event",
            "cloudera-workbench:other-owner",
            "other-session",
            "other",
        )
    )

    visible = client.get(
        "/api/v1/audit/events?organization_id=acme&include_all=true",
        headers=headers("admin"),
    )
    denied = client.get(
        "/api/v1/audit/events?organization_id=other&include_all=true",
        headers=headers("admin"),
    )

    assert visible.status_code == 200
    assert {item["id"] for item in visible.json()["items"]} == {
        "owner-event",
        "viewer-event",
    }
    assert "audit.read_organization" in visible.json()["available_actions"]
    assert denied.status_code == 403

    diagnostic_event = event(
        "diagnostic-event",
        "cloudera-workbench:viewer",
        "viewer-session",
    )
    repository.append_audit_event(
        AuditEvent(
            **{
                **diagnostic_event.__dict__,
                "details": {
                    "error_code": "query_unavailable",
                    "diagnostics": {
                        "stage": "impala_query",
                        "exception_chain": [
                            {
                                "exception_type": "OperationalError",
                                "description": "connection refused",
                            }
                        ],
                    },
                },
            }
        )
    )
    detail = client.get(
        "/api/v1/audit/events/diagnostic-event",
        headers=headers("admin"),
    )

    assert detail.status_code == 200
    assert detail.json()["details"] == {
        "error_code": "query_unavailable"
    }
    assert detail.json()["diagnostics"]["stage"] == "impala_query"
    assert (
        detail.json()["diagnostics"]["exception_chain"][0]["exception_type"]
        == "OperationalError"
    )


def test_audit_api_validates_sessions_and_client_event_allowlist(audit_client):
    client, repository = audit_client
    invalid_session = client.get(
        "/api/v1/audit/events?session_id=not%20a%20valid%20id",
        headers=headers("owner"),
    )
    invalid_action = client.post(
        "/api/v1/audit/client-events",
        headers=headers("owner"),
        json={"action": "credential.capture"},
    )
    injected_details = client.post(
        "/api/v1/audit/client-events",
        headers=headers("owner"),
        json={
            "action": "navigation.view",
            "path": "/canvas",
            "details": {"token": "must-not-be-accepted"},
        },
    )
    accepted = client.post(
        "/api/v1/audit/client-events",
        headers=headers("owner"),
        json={
            "action": "canvas.node_focus",
            "resource_type": "node",
            "resource_id": "dataset:customers",
            "model_id": "customer360",
        },
    )

    assert invalid_session.status_code == 422
    assert invalid_action.status_code == 422
    assert injected_details.status_code == 422
    assert accepted.status_code == 200
    stored, _ = repository.audit_events(
        principal_id="cloudera-workbench:owner",
        component="ui",
        offset=0,
        limit=20,
    )
    focused = next(item for item in stored if item.action == "canvas.node_focus")
    assert focused.details == {}
    assert focused.model_id == "customer360"


def test_job_audit_records_success_and_failure_without_error_contents(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "job-audit.db"
    monkeypatch.setenv("HELIOS_METADATA_DB", str(database))
    monkeypatch.setenv("CDSW_USER", "job-owner")
    monkeypatch.setenv("CDSW_JOB_ID", "job-session")
    from jobs._common import audit_job

    with audit_job("profile", "run-success"):
        pass
    with pytest.raises(RuntimeError, match="private failure text"):
        with audit_job("propose", "run-failure"):
            raise RuntimeError("private failure text")

    repository = SQLiteMetadataRepository(database)
    events, total = repository.audit_events(
        principal_id="cloudera-workbench:job-owner",
        component="job",
        offset=0,
        limit=20,
    )

    assert total == 4
    assert {item.outcome for item in events} == {
        "started",
        "success",
        "error",
    }
    failed = next(item for item in events if item.outcome == "error")
    assert failed.details["error_type"] == "RuntimeError"
    assert "private failure text" not in str(failed.details)
