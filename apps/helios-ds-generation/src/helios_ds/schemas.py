"""Lakehouse schema definitions for Helios-DS.

This module defines the Iceberg table schemas for:
- helios_ds.*: Generation artifacts and metadata
- helios_ground_truth.*: Hidden answer key
- helios_index.*: What Helios crawler discovers (written by crawler, not generator)
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# ============================================================================
# helios_ds.* schemas (Generation artifacts & metadata)
# ============================================================================


class DatasetRecord(BaseModel):
    """Canonical dataset identity. Exactly one row per dataset; no wall-clock fields
    (operational timing lives in generation_runs and dataset_lifecycle)."""

    dataset_id: str = Field(..., description="UUID v5 of the generation identity")
    config_hash: str = Field(..., description="SHA256 of canonical config")
    config_json: str = Field(..., description="Canonical config JSON")
    template_bundle_hash: str = Field(..., description="SHA256 of template bundle")
    source_fingerprint_hash: str = Field(..., description="SHA256 of source_fingerprint_json")
    source_fingerprint_json: str = Field(..., description="Per-table TPC-DS source identity")
    generator_version: str = Field(..., description="Generator code version")
    generator_schema_version: str = Field(..., description="ID/seed derivation rules version")
    python_version: str = Field(..., description="Python runtime version")
    container_digest: Optional[str] = Field(None, description="Runtime image digest")
    manifest_locator: Dict[str, Any] = Field(..., description="Where the manifest object is")
    manifest_sha256: str = Field(..., description="SHA256 of canonical manifest bytes")
    scenario_count: int = Field(...)
    planned_artifact_count: int = Field(...)


class GenerationRunRecord(BaseModel):
    """One operational generation attempt."""

    run_id: str = Field(...)
    dataset_id: str = Field(...)
    job_id: Optional[str] = Field(None)
    started_at: str = Field(..., description="ISO 8601 timestamp")
    finished_at: str = Field(..., description="ISO 8601 timestamp")
    outcome: str = Field(..., description="SUCCEEDED or FAILED")
    error: Optional[str] = Field(None)
    python_version: str = Field(...)
    platform: str = Field(...)
    container_digest: Optional[str] = Field(None)


class DatasetLifecycleRecord(BaseModel):
    """Append-only dataset state transition. Current state = highest event_seq."""

    dataset_id: str = Field(...)
    event_seq: int = Field(..., description="1-based, per dataset")
    state: str = Field(..., description="CREATING, VALIDATING, IN_REVIEW, READY, FAILED, REJECTED")
    run_id: Optional[str] = Field(None)
    actor: str = Field(..., description="Principal or service that made the transition")
    occurred_at: str = Field(..., description="ISO 8601 timestamp")
    reason: Optional[str] = Field(None)


class ScenarioPlanRecord(BaseModel):
    """Deterministic scenario plan (generator-internal; not crawler-visible)."""

    dataset_id: str = Field(...)
    scenario_id: str = Field(...)
    scenario_type: str = Field(...)
    business_key: str = Field(..., description="Canonical TPC-DS business key")
    rank_score: str = Field(..., description="Candidate ranking hash")
    scenario_seed: str = Field(...)
    source_refs: List[Dict[str, Any]] = Field(..., description="[{table, key}]")
    facts: Dict[str, Any] = Field(..., description="Canonical source record")
    artifact_plan: List[Dict[str, Any]] = Field(...)


class TemplateVersionRecord(BaseModel):
    """Template identity used by a dataset."""

    dataset_id: str = Field(...)
    template_id: str = Field(...)
    template_version: str = Field(...)
    template_schema_version: str = Field(...)
    artifact_type: str = Field(...)
    content_hash: str = Field(...)


class ArtifactRecord(BaseModel):
    """Artifact identity and provenance."""

    dataset_id: str = Field(...)
    artifact_id: str = Field(..., description="UUID v5 of artifact")
    scenario_id: str = Field(..., description="Parent scenario ID")
    artifact_type: str = Field(..., description="pdf, email, chat, image, audio, video")
    mime_type: str = Field(..., description="MIME type of artifact")
    source_locator: Dict[str, Any] = Field(
        ..., description="How to fetch: {connector_type, bucket, key, ...}"
    )
    sha256: str = Field(..., description="Content hash of artifact bytes")
    size_bytes: int = Field(...)
    semantic_timestamp: str = Field(..., description="ISO 8601 timestamp from TPC-DS context")
    template_id: str = Field(...)
    template_version: str = Field(...)
    acl_policy_id: str = Field(..., description="Reference to source_acl_bindings")


class ArtifactSourceRecord(BaseModel):
    """Artifact-to-TPC-DS source provenance."""

    dataset_id: str = Field(...)
    artifact_id: str = Field(...)
    source_table: str = Field(..., description="TPC-DS table name")
    source_key: Dict[str, Any] = Field(..., description="Business key columns")


class SourcePrincipalRecord(BaseModel):
    """Simulated user/group for ACL."""

    dataset_id: str = Field(...)
    principal_id: str = Field(...)
    principal_type: str = Field(..., description="user or group")
    name: str = Field(...)


class SourceACLBindingRecord(BaseModel):
    """Artifact-to-principal permission mapping."""

    dataset_id: str = Field(...)
    acl_policy_id: str = Field(...)
    artifact_id: str = Field(...)
    principal_id: str = Field(...)
    permission: str = Field(..., description="read, write, admin")


# ============================================================================
# helios_ground_truth.* schemas (Hidden answer key)
# ============================================================================


class TruthEntityRecord(BaseModel):
    """Intended enterprise entity."""

    dataset_id: str = Field(...)
    entity_id: str = Field(..., description="UUID v5")
    entity_type: str = Field(..., description="Item, Customer, Sale, etc.")
    source_key: Dict[str, Any] = Field(..., description="TPC-DS business key")
    canonical_name: str = Field(..., description="Display name")


class TruthEntityMentionRecord(BaseModel):
    """Expected surface mention in artifact."""

    dataset_id: str = Field(...)
    mention_id: str = Field(...)
    entity_id: str = Field(...)
    artifact_id: str = Field(...)
    surface_form: str = Field(..., description="Text/audio as it appears")
    modality: str = Field(..., description="visual, text, speech, etc.")
    start_offset: int = Field(..., description="Byte or character offset")
    end_offset: int = Field(...)


class TruthRelationshipRecord(BaseModel):
    """Intended graph edge."""

    dataset_id: str = Field(...)
    relationship_id: str = Field(...)
    source_entity_id: str = Field(...)
    predicate: str = Field(..., description="MENTIONS, SUPPORTS, RETURNS, etc.")
    target_entity_id: str = Field(...)
    confidence: float = Field(default=1.0)


class TruthClaimRecord(BaseModel):
    """Intended assertion."""

    dataset_id: str = Field(...)
    claim_id: str = Field(...)
    scenario_id: str = Field(...)
    claim_type: str = Field(..., description="PACKAGING_DAMAGED, HIGH_RETURN_RATE, etc.")
    subject: str = Field(...)
    obj: str = Field(...)
    truth_status: str = Field(
        ...,
        description=(
            "INTENDED_TRUE, INTENDED_FALSE, INTENDED_AMBIGUOUS, SOURCE_CLAIM_ONLY, "
            "CONTRADICTS_STRUCTURED_EVIDENCE"
        ),
    )


class TruthEvidenceRecord(BaseModel):
    """Exact location of supporting evidence."""

    dataset_id: str = Field(...)
    evidence_id: str = Field(...)
    claim_id: str = Field(...)
    artifact_id: str = Field(...)
    segment_id: Optional[str] = Field(None, description="Page, region, time-range, message ID")
    start_offset: Optional[int] = Field(None)
    end_offset: Optional[int] = Field(None)
    locator_type: str = Field(..., description="page_number, image_region, time_range, etc.")


class ExpectedQueryRecord(BaseModel):
    """Golden test question."""

    dataset_id: str = Field(...)
    query_id: str = Field(...)
    question: str = Field(...)
    principal_id: str = Field(..., description="Who asks this; ACL applies")
    required_structured: Optional[Dict[str, Any]] = Field(None, description="SQL requirement")
    required_entities: List[str] = Field(default_factory=list)
    required_artifacts: List[str] = Field(default_factory=list)
    required_claims: List[str] = Field(default_factory=list)


class ExpectedResultRecord(BaseModel):
    """Golden answer for evaluation."""

    dataset_id: str = Field(...)
    result_id: str = Field(...)
    query_id: str = Field(...)
    result_type: str = Field(..., description="sql_hash, artifact_set, graph_path, etc.")
    result_data: Dict[str, Any] = Field(...)


# ============================================================================
# helios_index.* schemas (Crawler-discovered knowledge)
# ============================================================================
# NOTE: These are written ONLY by the crawler, not the generator. They are
# kept here as documentation of the contract; the generator never creates
# or writes helios_index tables.
# The generator creates ground truth; the crawler reads artifacts and
# discovers what it can. The gap is measurable.


class DiscoveredAssetRecord(BaseModel):
    """Asset discovered by crawler."""

    asset_id: str = Field(..., description="Stable identity for this logical artifact")
    asset_type: str = Field(...)
    source_locator: Dict[str, Any] = Field(...)
    sha256: str = Field(..., description="Actual content hash from source")


class DiscoveredSegmentRecord(BaseModel):
    """Page, image region, time range, or message."""

    segment_id: str = Field(...)
    asset_id: str = Field(...)
    segment_type: str = Field(..., description="page, image_region, time_range, message")
    locator: Dict[str, Any] = Field(..., description="Segment-specific locator")


class DiscoveredEntityMentionRecord(BaseModel):
    """Extracted reference before resolution."""

    mention_id: str = Field(...)
    segment_id: str = Field(...)
    surface_form: str = Field(...)
    entity_type_proposed: Optional[str] = Field(None)


class DiscoveredEntityRecord(BaseModel):
    """Resolved enterprise entity."""

    entity_id: str = Field(..., description="Discovered or inferred ID")
    entity_type: str = Field(...)
    canonical_name: str = Field(...)
    confidence: float = Field(default=0.0)


class DiscoveredRelationshipRecord(BaseModel):
    """Discovered edge."""

    relationship_id: str = Field(...)
    source_entity_id: str = Field(...)
    predicate: str = Field(...)
    target_entity_id: str = Field(...)
    evidence_segment_id: Optional[str] = Field(None)
    confidence: float = Field(default=0.0)


class DiscoveredClaimRecord(BaseModel):
    """Extracted assertion."""

    claim_id: str = Field(...)
    claim_type: str = Field(...)
    subject: str = Field(...)
    obj: str = Field(...)
    confidence: float = Field(default=0.0)
