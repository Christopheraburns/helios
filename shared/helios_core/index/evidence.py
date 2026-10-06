"""Searching what a crawl found (CR-11): segment embeddings and entity claims.

``embed_run`` embeds a crawl's passages into a LanceDB table on the project
filesystem, replacing the same source's rows from its previous crawl, so the
table always holds each source's latest crawl. ``search`` finds passages by
meaning, one per document.

A passage is what a reader would want back, not always one segment
(``passages``): an email is one passage, its body under its subject and sender;
a chat is overlapping windows of consecutive messages with who said them; a PDF
is one passage per page. Subject and sender lines are never passages by
themselves, because a short title outranks the text it introduces.

Each row carries the entities its document is linked to (class, name
and warehouse keys), which is what lets an answer join documents to tables.

``entity_claims`` reads claims and their evidence from ``helios_index``.

The embedding model runs locally (sentence-transformers); nothing leaves the
project. The table name includes the model, so changing the model starts a new
table instead of mixing vectors.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from . import runs
from .records import (
    ClaimEvidenceRecord,
    ClaimRecord,
    CrawlRunRecord,
    EntityRecord,
    RelationshipRecord,
    SegmentRecord,
)
from .store import IndexStore

MODEL_NAME = os.environ.get("HELIOS_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
MAX_ENTITIES_PER_ASSET = 12
CHAT_WINDOW = 6  # messages per chat passage
CHAT_STRIDE = 3  # messages between the starts of consecutive passages
ENTITIES = "helios_index.entities"
CLAIMS = "helios_index.claims"
CLAIM_EVIDENCE = "helios_index.claim_evidence"

Embedder = Callable[[list[str]], list[list[float]]]


class EvidenceUnavailable(RuntimeError):
    """Nothing has been embedded yet, or the libraries are not installed."""


def location() -> str:
    root = os.environ.get("HELIOS_ROOT") or os.path.join(
        os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios"
    )
    return os.environ.get("HELIOS_EVIDENCE_INDEX") or os.path.join(root, "state", "evidence.lance")


def table_name(model: str = MODEL_NAME) -> str:
    return "segments_" + re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")


LOGGER = logging.getLogger(__name__)
_models: dict[str, Any] = {}
_model_lock = threading.Lock()


def default_embedder(texts: list[str]) -> list[list[float]]:
    with _model_lock:  # a search arriving during warm-up waits for it instead of loading twice
        if MODEL_NAME not in _models:
            from sentence_transformers import SentenceTransformer

            _models[MODEL_NAME] = SentenceTransformer(MODEL_NAME)
    vectors = _models[MODEL_NAME].encode(
        texts, normalize_embeddings=True, batch_size=64, show_progress_bar=False
    )
    return [vector.tolist() for vector in vectors]


def warm_up() -> threading.Thread | None:
    """Load the embedding model in the background. Loading takes minutes on a
    small pod, longer than a conversation's delegated credential lasts, so a
    server that answers searches loads it at startup instead of on first use."""
    import importlib.util

    if any(importlib.util.find_spec(m) is None for m in ("lancedb", "sentence_transformers")):
        return None

    def load() -> None:
        try:
            default_embedder(["warm up"])
            LOGGER.info("embedding model %s loaded", MODEL_NAME)
        except Exception:
            LOGGER.exception("loading embedding model %s failed", MODEL_NAME)

    thread = threading.Thread(target=load, name="helios-embedding-warm-up", daemon=True)
    thread.start()
    return thread


def _connect(path: str | None):
    try:
        import lancedb
    except ImportError as exc:
        raise EvidenceUnavailable(f"lancedb is not installed: {exc}") from exc
    return lancedb.connect(path or location())


def _table(db: Any, name: str) -> Any | None:
    try:
        return db.open_table(name)
    except Exception:  # noqa: BLE001 - lancedb raises ValueError or its own error by version
        return None


def _quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def asset_entities(
    entities: Iterable[EntityRecord], relationships: Iterable[RelationshipRecord]
) -> dict[str, list[dict[str, Any]]]:
    """The entities each asset is about or mentions, About first."""
    by_id = {e.entity_id: e for e in entities}
    linked: dict[str, list[dict[str, Any]]] = {}
    ordered = sorted(
        (
            r
            for r in relationships
            if r.source_kind == "asset" and r.target_kind == "entity" and r.target_id in by_id
        ),
        key=lambda r: (r.relationship_type != "About", r.relationship_id),
    )
    for relationship in ordered:
        entity = by_id[relationship.target_id]
        found = linked.setdefault(relationship.source_id, [])
        if len(found) < MAX_ENTITIES_PER_ASSET and all(
            e["entity_id"] != entity.entity_id for e in found
        ):
            found.append(
                {
                    "entity_id": entity.entity_id,
                    "class": entity.ontology_class,
                    "name": entity.canonical_name,
                    "keys": list(entity.external_ids),
                }
            )
    return linked


def _email_passage(parts: list[SegmentRecord]) -> dict[str, Any] | None:
    bodies = [s for s in parts if s.segment_type == "email_body" and s.text.strip()]
    subject = next((s for s in parts if s.segment_type == "email_subject" and s.text.strip()), None)
    sender = next((s for s in parts if s.segment_type == "email_header" and s.text.strip()), None)
    anchor = bodies[0] if bodies else subject
    if anchor is None:
        return None
    heading = [f"Subject: {subject.text.strip()}"] if subject else []
    if sender:
        heading.append(f"From: {sender.text.strip()}")
    body = "\n\n".join(s.text.strip() for s in bodies)
    return {
        "segment_id": anchor.segment_id,
        "segment_type": "email",
        "locator": anchor.locator,
        "text": "\n\n".join(part for part in ("\n".join(heading), body) if part),
    }


def _chat_passages(messages: list[SegmentRecord]) -> list[dict[str, Any]]:
    def line(message: SegmentRecord) -> str:
        who = message.structure.get("sender_name") or message.structure.get("sender") or ""
        role = message.structure.get("role")
        speaker = f"{who} ({role})" if who and role else who
        return f"{speaker}: {message.text.strip()}" if speaker else message.text.strip()

    messages = [m for m in messages if m.text.strip()]
    starts = range(0, max(len(messages) - CHAT_WINDOW, 0) + CHAT_STRIDE, CHAT_STRIDE)
    found = []
    for start in starts:
        window = messages[start : start + CHAT_WINDOW]
        if not window or (start and len(window) <= CHAT_WINDOW - CHAT_STRIDE):
            continue  # nothing new past the previous window
        found.append(
            {
                "segment_id": window[0].segment_id,
                "segment_type": "chat",
                "locator": {**window[0].locator, "messages": len(window)},
                "text": "\n".join(line(m) for m in window),
            }
        )
    return found


def passages(segments: Sequence[SegmentRecord]) -> list[dict[str, Any]]:
    """The searchable passages of a crawl's segments, in document order."""
    by_asset: dict[str, list[SegmentRecord]] = {}
    for segment in segments:
        by_asset.setdefault(segment.asset_id, []).append(segment)
    found: list[dict[str, Any]] = []
    for asset_id, parts in by_asset.items():
        parts = sorted(parts, key=lambda s: s.ordinal)
        emails = [s for s in parts if s.segment_type.startswith("email_")]
        chats = [s for s in parts if s.segment_type == "message"]
        built = [p for p in [_email_passage(emails)] if p] if emails else []
        built += _chat_passages(chats)
        built += [
            {
                "segment_id": s.segment_id,
                "segment_type": s.segment_type,
                "locator": s.locator,
                "text": s.text.strip(),
            }
            for s in parts
            if s not in emails and s not in chats and s.text.strip()
        ]
        found += [{**p, "asset_id": asset_id} for p in built]
    return found


def embed_run(
    run: CrawlRunRecord,
    segments: Sequence[SegmentRecord],
    entities: Sequence[EntityRecord] = (),
    relationships: Sequence[RelationshipRecord] = (),
    *,
    embedder: Embedder | None = None,
    path: str | None = None,
) -> int:
    """Embed a run's passages and make them the source's searchable set.
    Returns how many passages were embedded."""
    wanted = passages(segments)
    db = _connect(path)
    name = table_name()
    table = _table(db, name)
    if wanted:
        linked = asset_entities(entities, relationships)
        vectors = (embedder or default_embedder)([p["text"] for p in wanted])
        rows = [
            {
                "segment_id": p["segment_id"],
                "crawl_run_id": run.crawl_run_id,
                "source": run.source,
                "asset_id": p["asset_id"],
                "segment_type": p["segment_type"],
                "locator": json.dumps(p["locator"], sort_keys=True),
                "text": p["text"],
                "entities": json.dumps(linked.get(p["asset_id"], []), sort_keys=True),
                "vector": [float(x) for x in vector],
            }
            for p, vector in zip(wanted, vectors, strict=True)
        ]
        if table is None:
            table = db.create_table(name, data=rows)
        else:
            table.add(rows)
    if table is not None:  # the source's previous crawl is no longer its latest
        table.delete(
            f"source = {_quoted(run.source)} AND crawl_run_id != {_quoted(run.crawl_run_id)}"
        )
    return len(wanted)


def search(
    query: str,
    limit: int = 5,
    *,
    source: str | None = None,
    embedder: Embedder | None = None,
    path: str | None = None,
) -> list[dict[str, Any]]:
    """Passages closest in meaning to ``query``, best first, at most one per
    document. ``relevance`` is cosine similarity (1 is identical in meaning,
    0 unrelated)."""
    table = _table(_connect(path), table_name())
    if table is None:
        raise EvidenceUnavailable("no crawl has been embedded yet")
    vector = (embedder or default_embedder)([query])[0]
    found = table.search(vector).distance_type("cosine")
    if source:
        found = found.where(f"source = {_quoted(source)}")
    best: dict[str, dict[str, Any]] = {}
    for row in found.limit(limit * (CHAT_WINDOW + 2)).to_list():
        if len(best) == limit:
            break
        best.setdefault(row["asset_id"], row)
    return [
        {
            "segment_id": row["segment_id"],
            "asset_id": row["asset_id"],
            "source": row["source"],
            "crawl_run_id": row["crawl_run_id"],
            "segment_type": row["segment_type"],
            "locator": json.loads(row["locator"]),
            "text": row["text"],
            "entities": json.loads(row["entities"]),
            "relevance": round(1.0 - float(row["_distance"]), 4),
        }
        for row in best.values()
    ]


# --- claims -------------------------------------------------------------------------

_run_cache: dict[str, tuple[list[Any], list[Any], list[Any]]] = {}


def _run_rows(store: IndexStore, crawl_run_id: str) -> tuple[list[Any], list[Any], list[Any]]:
    """A finished run's entities, claims and evidence. Finished runs never change."""
    if crawl_run_id not in _run_cache:
        where = {"crawl_run_id": crawl_run_id}
        _run_cache[crawl_run_id] = (
            store.read(ENTITIES, where),
            store.read(CLAIMS, where),
            store.read(CLAIM_EVIDENCE, where),
        )
    return _run_cache[crawl_run_id]


def latest_runs(store: IndexStore) -> list[CrawlRunRecord]:
    """Each source's latest successful deterministic crawl, newest first. Other
    strategies (the LLM crawler) are experiments to be scored, and do not feed
    search or browsing."""
    seen: dict[str, CrawlRunRecord] = {}
    for run in runs.runs(store):
        strategy = (run.settings or {}).get("strategy") or "deterministic"
        if run.status == "SUCCEEDED" and strategy == "deterministic" and run.source not in seen:
            seen[run.source] = run
    return list(seen.values())


def entity_claims(
    store: IndexStore,
    entity: str,
    predicate: str | None = None,
    *,
    max_entities: int = 5,
    max_claims: int = 25,
    max_evidence: int = 3,
) -> dict[str, Any]:
    """What the documents of each source's latest crawl claim about the entities
    matching ``entity``: an entity ID, a warehouse key
    ("tpcds.customer:c_customer_sk=12345") or part of a name."""
    needle = entity.strip().lower()
    matched: list[dict[str, Any]] = []
    claims_out: list[dict[str, Any]] = []
    total = 0
    for run in latest_runs(store):
        if not run.counts.get("claims") or len(matched) >= max_entities:
            continue
        entities, claims, evidence = _run_rows(store, run.crawl_run_id)
        by_id = {e.entity_id: e for e in entities if isinstance(e, EntityRecord)}

        def rank(e: EntityRecord) -> int | None:
            keys = [k.lower() for k in e.external_ids]
            name = e.canonical_name.lower()
            if needle in (e.entity_id.lower(), name) or needle in keys:
                return 0
            return 1 if needle and needle in name else None

        ranked = sorted(
            ((rank(e), e.canonical_name, e) for e in by_id.values() if rank(e) is not None),
            key=lambda item: item[:2],
        )
        chosen = [e for _, _, e in ranked[: max_entities - len(matched)]]
        if not chosen:
            continue
        ids = {e.entity_id for e in chosen}
        matched += [
            {
                "entity_id": e.entity_id,
                "class": e.ontology_class,
                "name": e.canonical_name,
                "keys": list(e.external_ids),
                "source": run.source,
            }
            for e in chosen
        ]
        passages: dict[str, list[dict[str, Any]]] = {}
        for item in evidence:
            if isinstance(item, ClaimEvidenceRecord):
                passages.setdefault(item.claim_id, []).append(
                    {"asset_id": item.asset_id, "locator": item.locator, "excerpt": item.excerpt}
                )

        def named(entity_id: str | None) -> dict[str, Any] | None:
            found = by_id.get(entity_id or "")
            return (
                {"entity_id": found.entity_id, "class": found.ontology_class, "name": found.canonical_name}
                if found
                else None
            )

        for claim in sorted(
            (c for c in claims if isinstance(c, ClaimRecord)), key=lambda c: (c.predicate, c.claim_id)
        ):
            if claim.subject_entity_id not in ids and claim.object_entity_id not in ids:
                continue
            if predicate and claim.predicate.lower() != predicate.strip().lower():
                continue
            total += 1
            if len(claims_out) < max_claims:
                found = passages.get(claim.claim_id, [])
                claims_out.append(
                    {
                        "predicate": claim.predicate,
                        "subject": named(claim.subject_entity_id),
                        "object": named(claim.object_entity_id),
                        "object_value": claim.object_value,
                        "confidence": claim.confidence,
                        "evidence_count": len(found),
                        "evidence": found[:max_evidence],
                    }
                )
    return {"entity": entity, "entities": matched, "claims_total": total, "claims": claims_out}
