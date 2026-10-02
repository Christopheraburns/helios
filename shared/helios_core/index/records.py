"""Row models for helios_index tables. Column order is the model's field order;
new fields are only ever appended, so existing tables migrate additively.

Index rows (everything but the ontology tables) carry the crawl run that wrote
them and the ontology version they were made under. A crawl run is one index
generation: readers use one run's rows at a time.

Locators follow the Helios-DS ground-truth conventions so scoring compares like
with like: email ``{"part": "body"|"subject", "start", "end"}`` (body offsets
with "\\n" line endings) or ``{"header": "From"}``; chat ``{"message_id",
"start", "end"}``; PDF ``{"page", "text"}`` or ``{"page", "start", "end"}``
within the page's extracted text.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# --- ontology versions (CR-0c) --------------------------------------------------


class OntologyVersionRecord(BaseModel):
    """A published ontology version (CR-0c). Immutable once written."""

    version: str
    content_hash: str = Field(description="SHA-256 of the canonical graph JSON")
    schema_path: str = Field(description="Root LinkML schema, relative to the repo")
    published_at: str
    published_by: str
    node_count: int
    edge_count: int
    graph_json: str = Field(description="The normalised OntologyGraph, canonical JSON")


class OntologyActivationRecord(BaseModel):
    """Append-only: the latest row is the active ontology version."""

    version: str
    content_hash: str
    activated_at: str
    activated_by: str


# --- crawl runs -------------------------------------------------------------------


class CrawlRunRecord(BaseModel):
    """One crawl: what was crawled, under which ontology, and how it ended.
    Append-only; a run's final state is its row with the latest ``recorded_at``."""

    crawl_run_id: str
    connector: str = Field(description='e.g. "helios_ds_s3"')
    source: str = Field(description="What was crawled, e.g. a Helios-DS dataset_id")
    ontology_version: str
    crawler_version: str
    status: str = Field(description="RUNNING, SUCCEEDED or FAILED")
    started_at: str
    recorded_at: str
    actor: str
    finished_at: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)
    error: str | None = None
    # Added with CR-0e: the crawler settings version the run used.
    settings_version: int | None = None
    settings_hash: str | None = None


# --- what was read ------------------------------------------------------------------


class AssetRecord(BaseModel):
    """An information asset as crawled (ontology InformationAsset)."""

    crawl_run_id: str
    asset_id: str = Field(description="Stable across runs: the connector's ID for the asset")
    asset_version_id: str = Field(description="Content hash; changes when the content changes")
    connector: str
    source: str
    ontology_class: str = Field(description="Document, Message, ...")
    mime_type: str
    source_locator: dict[str, Any]
    size_bytes: int
    semantic_timestamp: str | None = None
    ontology_version: str = ""
    # Added with CR-2: what happened to the asset in this run.
    status: str = Field(
        "fetched",
        description=(
            "fetched, carried_forward (unchanged since the last run), integrity_failed, "
            "missing, fetch_failed (storage error after retries), unsupported, "
            "type_mismatch, no_text, invalid, analyzed"
        ),
    )
    status_detail: str = ""


class SegmentRecord(BaseModel):
    """An addressable part of an asset: a PDF page, an email part, a chat message."""

    crawl_run_id: str
    segment_id: str
    asset_id: str
    segment_type: str = Field(description="page, email_header, email_subject, email_body, message")
    ordinal: int
    locator: dict[str, Any]
    text: str
    ontology_version: str = ""
    # Added with CR-3: structure the analyzer recognised in the segment, e.g. PDF
    # label/value fields and table rows, or a chat message's sender and time.
    # Named "structure" because FIELDS is an Impala reserved word.
    structure: dict[str, Any] = Field(default_factory=dict)


# --- what was found -----------------------------------------------------------------


class MentionRecord(BaseModel):
    """A reference found in a segment, before or without resolution."""

    crawl_run_id: str
    mention_id: str
    asset_id: str
    segment_id: str
    surface_form: str
    locator: dict[str, Any]
    proposed_class: str | None = Field(None, description="Ontology class the extractor expects")
    extractor: str = Field(description="pattern, gazetteer or contextual")
    extractor_detail: str = Field("", description="Pattern name, or gazetteer source column")
    start_offset: int | None = None
    end_offset: int | None = None
    ontology_version: str = ""


class EntityRecord(BaseModel):
    """A resolved real-world entity. ``entity_id`` is Helios-assigned and stable
    for the same external key; ``external_ids`` carry source keys
    ("<source>.<object>:<key>", per ontology/README.md)."""

    crawl_run_id: str
    entity_id: str
    ontology_class: str
    canonical_name: str
    external_ids: list[str] = Field(default_factory=list)
    ontology_version: str = ""


class EntityLinkRecord(BaseModel):
    """A mention resolved (or nearly) to an entity: SameAs at or above the tier's
    threshold, PossiblySameAs below it. Every link records its tier and evidence."""

    crawl_run_id: str
    link_id: str
    mention_id: str
    entity_id: str
    link_type: str = Field(description="SameAs or PossiblySameAs")
    resolved_by: str = Field(description="exact_key, alias, fuzzy, joint, contextual, ...")
    score: float
    evidence_segment_ids: list[str] = Field(default_factory=list)
    ontology_version: str = ""


class RelationshipRecord(BaseModel):
    """An edge between two entities, or between an asset and an entity
    (``source_kind`` / ``target_kind`` say which), typed by an ontology
    relationship class (Mentions, About, ReturnOf, Contains, ...)."""

    crawl_run_id: str
    relationship_id: str
    relationship_type: str
    source_kind: str = Field(description="entity or asset")
    source_id: str
    target_kind: str = Field(description="entity or asset")
    target_id: str
    resolved_by: str
    confidence: float
    evidence_segment_ids: list[str] = Field(default_factory=list)
    ontology_version: str = ""


class ClaimRecord(BaseModel):
    """An assertion extracted from the documents, with a predicate from the
    ontology's claim vocabulary (e.g. RetailClaimPredicate)."""

    crawl_run_id: str
    claim_id: str
    predicate: str
    subject_entity_id: str
    object_entity_id: str | None = None
    object_value: str | None = None
    confidence: float
    extractor: str
    ontology_version: str = ""


class ClaimEvidenceRecord(BaseModel):
    """A passage supporting a claim."""

    crawl_run_id: str
    claim_id: str
    evidence_id: str
    asset_id: str
    segment_id: str
    locator: dict[str, Any]
    excerpt: str
    ontology_version: str = ""


# --- crawler settings (CR-0e) ---------------------------------------------------


class CrawlerSettingsRecord(BaseModel):
    """An immutable version of the crawler settings (helios_core.crawler.settings)."""

    version: int = Field(description="1, 2, 3, ... in order of saving")
    content_hash: str
    settings_json: str = Field(description="The CrawlerSettings document, canonical JSON")
    created_at: str
    created_by: str
    note: str = ""


class CrawlerSettingsActivationRecord(BaseModel):
    """Append-only: the latest row is the settings version crawls use."""

    version: int
    content_hash: str
    activated_at: str
    activated_by: str


# --- evaluations (CR-8 / CR-E1) ------------------------------------------------------


class EvaluationRecord(BaseModel):
    """One scoring of a crawl run against a Helios-DS ground-truth dataset
    (apps/helios/crawler/evaluate.py). Append-only: ``evaluation_id`` is derived
    from (crawl_run_id, dataset_id, harness_version), so a re-evaluation adds a
    row with the same ID and readers take the latest ``evaluated_at``; equal
    ``metrics`` across rows show the harness is reproducible."""

    evaluation_id: str
    crawl_run_id: str
    dataset_id: str = Field(description="The helios_ground_truth dataset scored against")
    evaluated_at: str
    evaluator: str = Field(description="The principal the harness ran as")
    evaluator_mode: str = Field(
        description='"proxy" (Impala delegation to the principal) or "workload_user"'
    )
    harness_version: str
    ontology_version: str
    strategy: str = Field(description="The run's crawler arm: deterministic, llm or hybrid")
    metrics: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict, description="The headline numbers")
    status: str = Field(description="SUCCEEDED or FAILED")
    error: str | None = None
