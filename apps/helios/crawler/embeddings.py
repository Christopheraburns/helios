"""CR-11: Embed crawl segments into LanceDB for semantic search.

Runs after segments are created. Each segment (email part, PDF page, chat message)
is embedded using a local deterministic model (all-MiniLM-L6-v2), stored with its
locators (for pointing back to the document), and made queryable by semantic
similarity.

The embeddings table is incremental: unchanged assets (carried_forward) reuse
their earlier embeddings; changed assets get new ones.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import lancedb
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
"""Deterministic, 384-dimensional, ~33MB. Runs locally, repeatable, no API."""

EMBEDDINGS_TABLE = "segment_embeddings"


class SegmentEmbedding(BaseModel):
    """One embedded segment, ready for LanceDB."""

    segment_id: str
    crawl_run_id: str
    asset_id: str
    text: str
    vector: list[float]
    locators: dict[str, Any]
    """e.g. {'page': 0} or {'part': 'body'} or {'message_id': '123'}"""
    content_hash: str
    """Hash of text, so unchanged segments across crawls can be skipped."""


def _get_db_path() -> Path:
    """Where to store LanceDB. Uses same location as helios_index."""
    from helios_core.index import index_location

    base = Path(index_location())
    db_path = base.parent / "lancedb"
    db_path.mkdir(parents=True, exist_ok=True)
    return db_path


def embedder() -> SentenceTransformer:
    """Load the embedding model. Cached after first load."""
    if not hasattr(embedder, "_model"):
        embedder._model = SentenceTransformer(EMBEDDING_MODEL)
    return embedder._model


def lancedb_connection() -> lancedb.DBConnection:
    """Open or create the LanceDB database."""
    db_path = _get_db_path()
    # LanceDB v1 uses .connect(uri) with a directory path
    return lancedb.connect(str(db_path))


def embed_segments(
    crawl_run_id: str,
    segments: list[dict],
    content_hashes: dict[str, str] | None = None,
) -> list[SegmentEmbedding]:
    """Embed a batch of segments from one crawl run.

    Args:
        crawl_run_id: The run being indexed
        segments: List of segment dicts from helios_index.segments, each with
                  segment_id, asset_id, text, locators
        content_hashes: Pre-computed hashes for unchanged segments (optional);
                       if None, computed from text

    Returns:
        List of SegmentEmbedding ready to store.
    """
    if not segments:
        return []

    model = embedder()
    embeddings = model.encode([s["text"] for s in segments], convert_to_tensor=False)

    result = []
    for segment, vector in zip(segments, embeddings):
        text_hash = content_hashes.get(segment["segment_id"]) if content_hashes else None
        if text_hash is None:
            text_hash = hashlib.sha256(segment["text"].encode()).hexdigest()[:16]

        result.append(
            SegmentEmbedding(
                segment_id=segment["segment_id"],
                crawl_run_id=crawl_run_id,
                asset_id=segment["asset_id"],
                text=segment["text"],
                vector=vector.tolist() if hasattr(vector, "tolist") else list(vector),
                locators=segment.get("locators", {}),
                content_hash=text_hash,
            )
        )

    return result


def save_embeddings(embeddings: list[SegmentEmbedding]) -> int:
    """Save embeddings to LanceDB, returning count stored.

    Upserts by segment_id to handle re-crawls of the same documents.
    """
    if not embeddings:
        return 0

    db = lancedb_connection()
    data = [e.model_dump() for e in embeddings]

    try:
        table = db.open_table(EMBEDDINGS_TABLE)
        table.add(data, mode="overwrite")
    except Exception:
        # Table doesn't exist; create it
        db.create_table(EMBEDDINGS_TABLE, data=data, mode="overwrite")

    return len(embeddings)


def search_segments(
    query_text: str,
    limit: int = 10,
    filter_dict: dict[str, Any] | None = None,
) -> list[dict]:
    """Search for segments by semantic similarity.

    Args:
        query_text: The search query
        limit: Max results to return
        filter_dict: Optional WHERE clause filters, e.g. {"asset_id": "..."}

    Returns:
        List of matching segments with scores, sorted by relevance.
    """
    db = lancedb_connection()
    try:
        table = db.open_table(EMBEDDINGS_TABLE)
    except Exception:
        return []  # No embeddings yet

    model = embedder()
    query_vector = model.encode([query_text], convert_to_tensor=False)[0]

    # LanceDB search by vector similarity
    results = table.search(query_vector).limit(limit)

    if filter_dict:
        for key, value in filter_dict.items():
            results = results.where(f"{key} = ?", value)

    rows = results.to_list()
    return [
        {
            "segment_id": row["segment_id"],
            "asset_id": row["asset_id"],
            "crawl_run_id": row["crawl_run_id"],
            "text": row["text"],
            "locators": row["locators"],
            "score": row.get("_distance", 0.0),  # LanceDB returns distance
        }
        for row in rows
    ]


def clear_run(crawl_run_id: str) -> None:
    """Remove all embeddings for a crawl run (e.g., if it is aborted)."""
    db = lancedb_connection()
    try:
        table = db.open_table(EMBEDDINGS_TABLE)
        table.delete(f"crawl_run_id = ?", crawl_run_id)
    except Exception:
        pass  # Table doesn't exist
