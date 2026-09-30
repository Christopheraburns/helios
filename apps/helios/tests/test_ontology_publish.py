"""Ontology publish endpoint tests."""

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

# Import just the router to avoid MCP module shadowing issues.
from apps.helios.console.ontology import ontology_router
from fastapi import FastAPI


@pytest.fixture
def temp_store():
    """Isolated temporary store for each test."""
    tmpdir = tempfile.mkdtemp(prefix="test_ontology_")
    with patch("apps.helios.graph.store.store_root", return_value=Path(tmpdir)):
        yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def app(temp_store):
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


class TestOntologyVersions:
    """Query endpoints for published versions."""

    def test_list_versions_empty_initially(self, client):
        """GET /api/v1/ontology/versions returns empty when nothing published."""
        response = client.get("/api/v1/ontology/versions")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_versions_after_publish(self, client):
        """Publishing a version makes it appear in the list."""
        # Publish.
        pub = client.post(
            "/api/v1/ontology:publish",
            json={
                "version": "0.1.0",
                "schema_path": "ontology/core/core.yaml",
            },
        )
        assert pub.status_code == 200

        # List.
        resp = client.get("/api/v1/ontology/versions")
        assert resp.status_code == 200
        versions = resp.json()
        assert len(versions) == 1
        assert versions[0]["version"] == "0.1.0"
        assert versions[0]["node_count"] > 0
        assert versions[0]["is_active"] is False  # Not activated yet

    def test_get_graph_returns_full_payload(self, client):
        """GET /api/v1/ontology/{version}/graph returns complete graph."""
        # Publish.
        client.post(
            "/api/v1/ontology:publish",
            json={
                "version": "0.1.0",
                "schema_path": "ontology/core/core.yaml",
            },
        )

        # Retrieve graph.
        resp = client.get("/api/v1/ontology/0.1.0/graph")
        assert resp.status_code == 200
        graph = resp.json()
        assert "version" in graph
        assert "content_hash" in graph
        assert "nodes" in graph
        assert "edges" in graph
        assert len(graph["nodes"]) > 0
        assert len(graph["edges"]) > 0

    def test_get_graph_nonexistent_version(self, client):
        """GET nonexistent version returns 404."""
        resp = client.get("/api/v1/ontology/99.99.99/graph")
        assert resp.status_code == 404

    def test_get_class_detail_returns_class_and_attributes(self, client):
        """GET /api/v1/ontology/{version}/classes/{name} returns class details."""
        # Publish core schema.
        client.post(
            "/api/v1/ontology:publish",
            json={
                "version": "0.1.0",
                "schema_path": "ontology/core/core.yaml",
            },
        )

        # Get Organization class.
        resp = client.get("/api/v1/ontology/0.1.0/classes/Organization")
        assert resp.status_code == 200
        data = resp.json()

        # Verify class details.
        assert "class" in data
        assert data["class"]["name"] == "Organization"
        assert "parents" in data  # Is_a edges
        assert "attributes" in data  # HAS_ATTRIBUTE edges
        assert "ranges" in data  # RANGE edges
        assert len(data["attributes"]) > 0  # Organization has attributes

    def test_get_class_detail_nonexistent_class(self, client):
        """GET nonexistent class returns 404."""
        # Publish core schema.
        client.post(
            "/api/v1/ontology:publish",
            json={
                "version": "0.1.0",
                "schema_path": "ontology/core/core.yaml",
            },
        )

        # Get nonexistent class.
        resp = client.get("/api/v1/ontology/0.1.0/classes/NonExistentClass")
        assert resp.status_code == 404

    def test_get_class_detail_nonexistent_version(self, client):
        """GET class from nonexistent version returns 404."""
        resp = client.get("/api/v1/ontology/99.99.99/classes/Organization")
        assert resp.status_code == 404
