import json

import pytest
from fastapi.testclient import TestClient

from apps.console.main import app
from helios_core import health as system_health
from helios_core import runs as runstore
from helios_core.graph import ArtifactGraphRepository


def headers(subject):
    return {"x-forwarded-user": subject}


@pytest.fixture
def persistent_client(persistent_auth_stack):
    previous = dict(app.state._state)
    app.state._state.pop("resource_store", None)
    app.state._state.pop("authorization_policy", None)
    app.state._state.pop("graph_repository", None)
    app.state.metadata_repository = persistent_auth_stack.repository
    app.state.graph_repository = ArtifactGraphRepository(
        persistent_auth_stack.artifacts
    )
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.state._state.clear()
        app.state._state.update(previous)


def test_real_dependencies_return_authentication_and_lookup_statuses(
    persistent_client,
):
    missing_identity = persistent_client.get(
        "/api/v1/models/customer360"
    )
    missing_resource = persistent_client.get(
        "/api/v1/models/does-not-exist",
        headers=headers("owner"),
    )
    denied = persistent_client.get(
        "/api/v1/models/customer360",
        headers=headers("outsider"),
    )
    allowed = persistent_client.get(
        "/api/v1/models/customer360",
        headers=headers("owner"),
    )

    assert missing_identity.status_code == 401
    assert missing_resource.status_code == 404
    assert denied.status_code == 403
    assert allowed.status_code == 200


def test_persisted_roles_drive_available_actions(persistent_client):
    owner = persistent_client.get(
        "/api/v1/models/customer360",
        headers=headers("owner"),
    ).json()
    viewer = persistent_client.get(
        "/api/v1/models/customer360",
        headers=headers("viewer"),
    ).json()
    editor = persistent_client.get(
        "/api/v1/models/finance",
        headers=headers("editor"),
    ).json()

    assert {"model.edit", "model.delete", "model.publish"} <= set(
        owner["available_actions"]
    )
    assert "model.read" in viewer["available_actions"]
    assert "model.edit" not in viewer["available_actions"]
    assert "model.edit" in editor["available_actions"]
    assert "model.delete" not in editor["available_actions"]


def test_model_overview_combines_authorized_graph_and_operational_metadata(
    persistent_client,
):
    response = persistent_client.get(
        "/api/v1/models/customer360/overview",
        headers=headers("owner"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "active"
    assert body["creator"] == {
        "id": "cloudera-workbench:owner",
        "display_name": "Owner",
    }
    assert body["data_sources"] == [
        {
            "data_source_id": "shared-warehouse",
            "name": "Shared warehouse",
            "connector": "impala",
            "selected_assets": ["crm.customers", "sales.orders"],
        }
    ]
    assert body["summary"] == {
        "dataset_count": 1,
        "relationship_count": 0,
        "concept_count": 0,
        "metric_count": 1,
    }
    assert body["lifecycle"] == {
        "publication_state": "published",
        "discovery_status": "unavailable",
        "review_status": "not_available",
        "unresolved_review_items": None,
        "latest_run_id": "customer-run",
    }
    assert "model.publish" in body["available_actions"]


def test_model_overview_uses_model_authorization(persistent_client):
    response = persistent_client.get(
        "/api/v1/models/customer360/overview",
        headers=headers("outsider"),
    )

    assert response.status_code == 403


def test_model_status_limits_infrastructure_details_to_org_admin(
    persistent_client,
):
    checker_calls = []
    app.state.system_health_checker = lambda repository: checker_calls.append(
        repository
    ) or []

    viewer = persistent_client.get(
        "/api/v1/models/customer360/status",
        headers=headers("viewer"),
    )
    owner_details = persistent_client.get(
        "/api/v1/models/customer360/status",
        params={"details": "true"},
        headers=headers("owner"),
    )

    assert viewer.status_code == 200
    body = viewer.json()
    assert body["status"] == "degraded"
    assert body["details_available"] is False
    assert body["details"] == []
    assert {item["id"] for item in body["components"]} == {
        "api",
        "semantic-model",
        "discovery-profile",
    }
    assert checker_calls == []
    assert owner_details.status_code == 403


def test_model_status_reports_partial_failures_and_recent_runs(
    persistent_client,
    tmp_path,
    monkeypatch,
):
    run_id = "customer-run"
    run_directory = tmp_path / "runs" / run_id
    run_directory.mkdir(parents=True)
    (run_directory / "harvest.json").write_text(
        json.dumps(
            {
                "model_id": "customer360",
                "harvested_at": "2026-09-20T10:00:00+00:00",
                "tables": [],
            }
        )
    )
    (run_directory / "profile.json").write_text(
        json.dumps(
            {
                "model_id": "customer360",
                "profiled_at": "2026-09-20T11:00:00+00:00",
                "tables": {},
            }
        )
    )
    monkeypatch.setattr(runstore, "RUNS_DIR", str(tmp_path / "runs"))
    app.state.system_health_checker = lambda _: [
        system_health.HealthComponent(
            "metadata",
            "Metadata repository",
            "healthy",
            "Metadata repository is available.",
        ),
        system_health.HealthComponent(
            "atlas",
            "Atlas",
            "unavailable",
            "Atlas connectivity check failed.",
        ),
        system_health.HealthComponent(
            "impala",
            "Impala data source",
            "healthy",
            "Impala connectivity check succeeded.",
        ),
    ]

    response = persistent_client.get(
        "/api/v1/models/customer360/status",
        params={"details": "true"},
        headers=headers("admin"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["details_available"] is True
    assert {item["id"] for item in body["details"]} == {
        "metadata",
        "atlas",
        "impala",
    }
    assert body["issues"] == [
        "Profiling completed; proposals are pending.",
        "Atlas connectivity check failed.",
    ]
    assert body["recent_activity"] == {
        "discovery": {
            "run_id": run_id,
            "completed_at": "2026-09-20T10:00:00+00:00",
        },
        "profile": {
            "run_id": run_id,
            "completed_at": "2026-09-20T11:00:00+00:00",
        },
    }


def test_graph_detail_is_lazy_and_uses_element_authorization(
    persistent_client,
):
    dataset = persistent_client.get(
        "/api/v1/models/customer360/graph/detail",
        params={"element_id": "dataset:customers"},
        headers=headers("viewer"),
    )
    attribute = persistent_client.get(
        "/api/v1/models/customer360/graph/detail",
        params={"element_id": "attribute:customers:customer_id"},
        headers=headers("viewer"),
    )
    denied = persistent_client.get(
        "/api/v1/models/customer360/graph/detail",
        params={"element_id": "dataset:customers"},
        headers=headers("outsider"),
    )

    assert dataset.status_code == 200
    assert dataset.json()["details"] == {
        "physical_identity": "warehouse.customers",
        "schema": "warehouse",
        "relationship_count": 0,
        "data_source_ids": ["shared-warehouse"],
    }
    assert attribute.status_code == 200
    assert attribute.json()["details"]["physical_type"] == "String"
    assert denied.status_code == 403


def test_org_admin_inherits_only_within_persisted_organization(
    persistent_client,
):
    organization = persistent_client.get(
        "/api/v1/organizations/acme",
        headers=headers("admin"),
    )
    models = persistent_client.get(
        "/api/v1/organizations/acme/models",
        headers=headers("admin"),
    )
    foreign_model = persistent_client.get(
        "/api/v1/models/other-model",
        headers=headers("admin"),
    )

    assert organization.status_code == 200
    assert models.status_code == 200
    assert {model["id"] for model in models.json()["models"]} == {
        "customer360",
        "finance",
    }
    assert foreign_model.status_code == 403


@pytest.mark.parametrize(
    "suffix, expected_key, expected_value",
    [
        ("glossary", "glossary_id", "customer-glossary"),
        ("semantic", "semantic_model_id", "customer-semantic"),
        ("ontology", "ontology_id", "customer-ontology"),
    ],
)
def test_nested_resources_use_persisted_model_scope(
    persistent_client,
    suffix,
    expected_key,
    expected_value,
):
    allowed = persistent_client.get(
        f"/api/v1/models/customer360/{suffix}",
        headers=headers("viewer"),
    )
    denied = persistent_client.get(
        f"/api/v1/models/finance/{suffix}",
        headers=headers("viewer"),
    )

    assert allowed.status_code == 200
    assert allowed.json()[expected_key] == expected_value
    assert denied.status_code == 403


def test_runs_and_versions_endpoints_are_authorized_by_persisted_grants(
    persistent_client,
):
    runs = persistent_client.get(
        "/api/v1/models/customer360/runs",
        headers=headers("viewer"),
    )
    versions = persistent_client.get(
        "/api/v1/models/customer360/versions",
        headers=headers("viewer"),
    )
    denied_runs = persistent_client.get(
        "/api/v1/models/finance/runs",
        headers=headers("viewer"),
    )

    assert runs.status_code == 200
    assert versions.status_code == 200
    assert denied_runs.status_code == 403


def test_run_contracts_project_real_artifacts_and_historical_profile(
    persistent_client,
    tmp_path,
    monkeypatch,
):
    run_id = "customer-run"
    run_directory = tmp_path / "runs" / run_id
    run_directory.mkdir(parents=True)
    (run_directory / "harvest.json").write_text(
        json.dumps(
            {
                "model_id": "customer360",
                "harvested_at": "2026-09-20T10:00:00+00:00",
                "engine": "impala",
                "databases": ["crm"],
                "tables": [
                    {
                        "database": "crm",
                        "table": "customers",
                        "columns": [{"name": "customer_id", "type": "bigint"}],
                    }
                ],
                "glossary_terms": [
                    {
                        "name": "Customer",
                        "columns": ["crm.customers.customer_id@cluster"],
                    }
                ],
            }
        )
    )
    (run_directory / "profile.json").write_text(
        json.dumps(
            {
                "model_id": "customer360",
                "profiled_at": "2026-09-20T10:01:00+00:00",
                "engine": "impala",
                "tables": {
                    "crm.customers": {
                        "stats": {
                            "row_count": 12,
                            "columns": {
                                "customer_id": {
                                    "type": "bigint",
                                    "null_rate": 0,
                                    "ndv_exact": 12,
                                }
                            },
                        }
                        ,
                        "primary_keys": [
                            {"column": "customer_id", "confidence": 0.95}
                        ],
                    }
                },
                "relationships": [
                    {
                        "from": "crm.orders",
                        "from_column": "customer_id",
                        "to": "crm.customers",
                        "to_column": "customer_id",
                        "match_ratio": 1.0,
                        "distinct_values": 12,
                        "secret": "must-not-leak",
                    }
                ],
                "suggested_relationships": [
                    {
                        "from": "crm.contacts",
                        "from_column": "customer_id",
                        "to": "crm.customers",
                        "to_column": "customer_id",
                        "match_ratio": 0.98,
                    }
                ],
                "rejected_candidates": [
                    {
                        "from": "crm.leads",
                        "from_column": "customer_id",
                        "to": "crm.customers",
                        "to_column": "customer_id",
                        "match_ratio": 0.1,
                        "unmatched": 9,
                    }
                ],
            }
        )
    )
    (run_directory / "propose.json").write_text(
        json.dumps(
            {
                "model_id": "customer360",
                "proposed_at": "2026-09-20T10:02:00+00:00",
                "llm": {"provider": "test", "model": "fixture", "calls": 1},
                "datasets": [
                    {
                        "table": "crm.customers",
                        "fields": [{"column": "customer_id"}],
                    }
                ],
                "relationships": [],
                "metrics": [],
                "glossary_terms": [],
            }
        )
    )
    monkeypatch.setattr(runstore, "RUNS_DIR", str(tmp_path / "runs"))

    collection = persistent_client.get(
        "/api/v1/models/customer360/runs",
        headers=headers("viewer"),
    )
    detail = persistent_client.get(
        f"/api/v1/models/customer360/runs/{run_id}",
        headers=headers("viewer"),
    )
    summary = persistent_client.get(
        f"/api/v1/models/customer360/runs/{run_id}/profile",
        headers=headers("viewer"),
    )
    profile = persistent_client.get(
        f"/api/v1/models/customer360/runs/{run_id}/profile/tables/crm.customers",
        headers=headers("viewer"),
    )

    assert collection.status_code == 200
    run = collection.json()["runs"][0]
    assert run["status"] == "completed"
    assert run["counts"]["discovered"] == {
        "tables": 1,
        "columns": 1,
        "glossary_terms": 1,
    }
    assert run["counts"]["proposed"]["fields"] == 1
    assert detail.status_code == 200
    assert [phase["status"] for phase in detail.json()["phases"]] == [
        "completed",
        "completed",
        "completed",
    ]
    assert detail.json()["provenance"]["llm"]["model"] == "fixture"
    assert summary.status_code == 200
    assert summary.json()["tables"] == [
        {
            "table_id": "crm.customers",
            "harvested": True,
            "profiled": True,
            "column_count": 1,
            "row_count": 12,
            "primary_key_candidates": [
                {"column": "customer_id", "confidence": 0.95}
            ],
        }
    ]
    assert summary.json()["relationships"][0]["match_ratio"] == 1.0
    assert "secret" not in summary.json()["relationships"][0]
    assert len(summary.json()["suggested_relationships"]) == 1
    assert len(summary.json()["rejected_candidates"]) == 1
    assert profile.status_code == 200
    assert profile.json()["row_count"] == 12
    assert profile.json()["columns"]["customer_id"]["ndv_exact"] == 12
    assert profile.json()["columns"]["customer_id"]["glossary_terms"] == [
        "Customer"
    ]
    assert profile.json()["primary_key_candidates"] == [
        {"column": "customer_id", "confidence": 0.95}
    ]
    assert profile.json()["relationships"][0]["from"] == "crm.orders"
    assert profile.json()["canvas"]["focus_node_id"] == "dataset:crm.customers"


def test_run_detail_enforces_model_ownership(persistent_client):
    response = persistent_client.get(
        "/api/v1/models/customer360/runs/not-owned",
        headers=headers("owner"),
    )

    assert response.status_code == 404


def test_run_routes_enforce_authentication_authorization_and_artifact_errors(
    persistent_client,
    tmp_path,
    monkeypatch,
):
    run_directory = tmp_path / "runs" / "customer-run"
    run_directory.mkdir(parents=True)
    (run_directory / "harvest.json").write_text(
        json.dumps(
            {
                "model_id": "customer360",
                "harvested_at": "not-a-timestamp",
                "tables": ["malformed-table", {"columns": "not-a-list"}],
                "databases": "not-a-list",
                "queries": "not-an-object",
                "glossary_terms": "not-a-list",
            }
        )
    )
    (run_directory / "profile.json").write_text("{malformed json")
    monkeypatch.setattr(runstore, "RUNS_DIR", str(tmp_path / "runs"))

    unauthenticated = persistent_client.get(
        "/api/v1/models/customer360/runs/customer-run"
    )
    denied = persistent_client.get(
        "/api/v1/models/customer360/runs/customer-run",
        headers=headers("outsider"),
    )
    collection = persistent_client.get(
        "/api/v1/models/customer360/runs",
        headers=headers("viewer"),
    )
    detail = persistent_client.get(
        "/api/v1/models/customer360/runs/customer-run",
        headers=headers("viewer"),
    )
    missing_profile = persistent_client.get(
        "/api/v1/models/customer360/runs/customer-run/profile/tables/crm.customers",
        headers=headers("viewer"),
    )
    malformed_summary = persistent_client.get(
        "/api/v1/models/customer360/runs/customer-run/profile",
        headers=headers("viewer"),
    )
    unauthenticated_summary = persistent_client.get(
        "/api/v1/models/customer360/runs/customer-run/profile"
    )
    summary_without_datasource_read = persistent_client.get(
        "/api/v1/models/customer360/runs/customer-run/profile",
        headers=headers("consumer"),
    )

    assert unauthenticated.status_code == 401
    assert denied.status_code == 403
    assert collection.status_code == 200
    run = collection.json()["runs"][0]
    assert run["status"] is None
    assert run["progress"] is None
    assert run["started_at"] is None
    assert run["completed_at"] is None
    assert run["phases"][0]["available"] is True
    assert run["phases"][0]["counts"] == {
        "tables": 1,
        "columns": 0,
        "glossary_terms": 0,
        "queries": 0,
    }
    assert detail.status_code == 200
    assert detail.json()["errors"] == []
    assert malformed_summary.status_code == 200
    assert malformed_summary.json()["tables"] == []
    assert malformed_summary.json()["relationships"] == []
    assert unauthenticated_summary.status_code == 401
    assert summary_without_datasource_read.status_code == 403
    assert missing_profile.status_code == 404
