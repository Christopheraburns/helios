"""Ontology API endpoints: publish, query, and browse."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from pydantic import BaseModel

from helios_core.ontology.parser import parse, ParseResult
from helios_core.ontology.graph import OntologyGraph, OntologyGraphError

LOGGER = logging.getLogger(__name__)
ontology_router = APIRouter(prefix="/api/v1/ontology", tags=["ontology"])


class PublishRequest(BaseModel):
    """Publish a LinkML schema as an ontology version."""

    version: str
    schema_path: str | None = None


class OntologyVersionResponse(BaseModel):
    """Response from publishing an ontology."""

    version: str
    content_hash: str
    node_count: int
    edge_count: int
    enum_count: int
    broken_mappings: list[dict[str, str]]


@ontology_router.post(":publish", response_model=OntologyVersionResponse)
async def publish_ontology(request: PublishRequest) -> OntologyVersionResponse:
    """Publish a LinkML schema as an ontology version.

    The schema file path must be relative to the repository root, e.g.
    "ontology/customers/example-tenant/extension.yaml". The parser resolves
    all imports and validates against the Ossie model spec.

    Returns:
        OntologyVersionResponse with version, hash, counts, and broken_mappings.
    """
    # Validate schema path.
    if not request.schema_path:
        raise HTTPException(status_code=400, detail="schema_path is required")

    # Find schema file relative to repo root.
    # __file__ is .../apps/helios/console/ontology.py
    # repo root is .../helios (three levels up)
    repo_root = Path(__file__).resolve().parents[3]
    schema_file = repo_root / request.schema_path

    if not schema_file.exists():
        raise HTTPException(
            status_code=400,
            detail=f"schema file not found: {request.schema_path}",
        )

    try:
        result: ParseResult = parse(str(schema_file), version=request.version)
    except Exception as e:
        LOGGER.exception("schema parse failed")
        raise HTTPException(
            status_code=400,
            detail=f"schema parse failed: {type(e).__name__}: {e}",
        )

    # Count nodes by label and edges.
    nodes_by_label = result.graph.nodes_by_label()
    enum_count = len(nodes_by_label.get("Enum", []))

    return OntologyVersionResponse(
        version=result.graph.version,
        content_hash=result.graph.content_hash,
        node_count=len(result.graph.nodes),
        edge_count=len(result.graph.edges),
        enum_count=enum_count,
        broken_mappings=result.broken_mappings,
    )


@ontology_router.get("/versions")
async def list_ontology_versions(request: Request) -> list[dict]:
    """List published ontology versions (stub for O-3)."""
    return []


@ontology_router.get("/{version}/graph")
async def get_ontology_graph(version: str, request: Request) -> dict:
    """Retrieve an ontology version's full graph (stub for O-3)."""
    raise HTTPException(status_code=501, detail="O-3: not yet implemented")


@ontology_router.get("/{version}/classes/{class_name}")
async def get_class_detail(version: str, class_name: str, request: Request) -> dict:
    """Retrieve a class and its attributes/relationships (stub for O-3)."""
    raise HTTPException(status_code=501, detail="O-3: not yet implemented")
