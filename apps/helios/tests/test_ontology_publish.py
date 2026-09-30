"""Ontology publish endpoint tests."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Import just the router to avoid MCP module shadowing issues.
from apps.helios.console.ontology import ontology_router
from fastapi import FastAPI


@pytest.fixture
def app():
    """Minimal FastAPI app with just the ontology router."""
    app = FastAPI()
    app.include_router(ontology_router)
    return app


@pytest.fixture
def client(app):
    """FastAPI test client."""
    return TestClient(app)


@pytest.fixture
def repo_root():
    """Repo root for schema paths."""
    return Path(__file__).resolve().parents[2]


class TestPublishOntology:
    """POST /api/v1/ontology:publish endpoint."""

    def test_publish_core_schema(self, client, repo_root):
        """Publish the core schema."""
        response = client.post(
            "/api/v1/ontology:publish",
            json={
                "version": "0.1.0",
                "schema_path": "ontology/core/core.yaml",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == "0.1.0"
        assert len(data["content_hash"]) == 64  # SHA-256 hex
        assert data["node_count"] > 0
        assert data["edge_count"] > 0

    def test_publish_extension_schema(self, client):
        """Publish the extension schema (includes all layers)."""
        response = client.post(
            "/api/v1/ontology:publish",
            json={
                "version": "0.1.0",
                "schema_path": "ontology/customers/example-tenant/extension.yaml",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == "0.1.0"
        # Extension should have classes from all layers.
        assert data["node_count"] > 40

    def test_publish_same_schema_same_hash(self, client):
        """Publishing the same schema twice gives the same hash (deterministic)."""
        payload = {
            "version": "0.1.0",
            "schema_path": "ontology/core/core.yaml",
        }
        r1 = client.post("/api/v1/ontology:publish", json=payload)
        r2 = client.post("/api/v1/ontology:publish", json=payload)
        assert r1.json()["content_hash"] == r2.json()["content_hash"]

    def test_publish_missing_schema_path(self, client):
        """Missing schema_path returns 400."""
        response = client.post(
            "/api/v1/ontology:publish",
            json={"version": "0.1.0"},
        )
        assert response.status_code == 400
        assert "schema_path is required" in response.text

    def test_publish_nonexistent_schema_file(self, client):
        """Nonexistent schema file returns 400."""
        response = client.post(
            "/api/v1/ontology:publish",
            json={
                "version": "0.1.0",
                "schema_path": "ontology/nonexistent/schema.yaml",
            },
        )
        assert response.status_code == 400
        assert "not found" in response.text


class TestOntologyStubs:
    """Stub endpoints for O-3."""

    def test_list_versions_returns_empty(self, client):
        """GET /api/v1/ontology/versions (stub)."""
        response = client.get("/api/v1/ontology/versions")
        assert response.status_code == 200
        assert response.json() == []

    def test_get_graph_not_implemented(self, client):
        """GET /api/v1/ontology/{version}/graph (O-3)."""
        response = client.get("/api/v1/ontology/0.1.0/graph")
        assert response.status_code == 501

    def test_get_class_detail_not_implemented(self, client):
        """GET /api/v1/ontology/{version}/classes/{name} (O-3)."""
        response = client.get("/api/v1/ontology/0.1.0/classes/Customer")
        assert response.status_code == 501
