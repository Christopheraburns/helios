"""Evaluation metric and result schemas for Helios-DS Evaluation."""
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MetricType(str, Enum):
    """Types of metrics computed during evaluation."""
    ENTITY_RECALL = "entity_recall"
    ENTITY_PRECISION = "entity_precision"
    RELATIONSHIP_RECALL = "relationship_recall"
    RELATIONSHIP_PRECISION = "relationship_precision"
    CLAIM_RECALL = "claim_recall"
    CLAIM_PRECISION = "claim_precision"
    ACL_DISCOVERY_RATE = "acl_discovery_rate"
    CROSS_SOURCE_RECOVERY = "cross_source_recovery"


class ComparisonResult(BaseModel):
    """Result of comparing one discovered item against ground truth."""
    discovered_id: str
    truth_id: Optional[str] = Field(None, description="Matched truth ID or None if unmatched")
    match_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Confidence of match")
    match_reason: Optional[str] = Field(None)
    is_correct: bool = Field(default=False)


class EntityMetrics(BaseModel):
    """Metrics for entity discovery."""
    total_discovered: int
    total_ground_truth: int
    correct_matches: int
    precision: float = Field(ge=0.0, le=1.0)
    recall: float = Field(ge=0.0, le=1.0)
    f1_score: float = Field(ge=0.0, le=1.0)


class RelationshipMetrics(BaseModel):
    """Metrics for relationship discovery."""
    total_discovered: int
    total_ground_truth: int
    correct_matches: int
    precision: float = Field(ge=0.0, le=1.0)
    recall: float = Field(ge=0.0, le=1.0)
    f1_score: float = Field(ge=0.0, le=1.0)


class ClaimMetrics(BaseModel):
    """Metrics for claim discovery."""
    total_discovered: int
    total_ground_truth: int
    correct_matches: int
    precision: float = Field(ge=0.0, le=1.0)
    recall: float = Field(ge=0.0, le=1.0)
    f1_score: float = Field(ge=0.0, le=1.0)

    # Claim-specific metrics
    truth_status_accuracy: float = Field(ge=0.0, le=1.0, description="Accuracy of INTENDED_TRUE vs FALSE")


class ACLMetrics(BaseModel):
    """Metrics for ACL/access-control discovery."""
    total_acl_bindings_ground_truth: int
    total_acl_bindings_discovered: int
    permissions_correct: int
    permission_accuracy: float = Field(ge=0.0, le=1.0)

    # Principal discovery
    principals_ground_truth: int
    principals_discovered: int
    principal_recall: float = Field(ge=0.0, le=1.0)


class ScenarioEvaluationResult(BaseModel):
    """Evaluation result for a single scenario."""
    scenario_id: str
    scenario_type: str

    # Aggregated metrics
    entity_metrics: EntityMetrics
    relationship_metrics: RelationshipMetrics
    claim_metrics: ClaimMetrics
    acl_metrics: ACLMetrics

    # Difficulty profile breakdown (if applicable)
    difficulty_profile: Optional[str] = None

    # Derived summary metrics
    overall_f1: float = Field(ge=0.0, le=1.0)
    artifacts_evaluated: int
    errors: List[str] = Field(default_factory=list)


class DatasetEvaluationResult(BaseModel):
    """Aggregated evaluation result for entire dataset."""
    dataset_id: str
    generation_version: str
    evaluation_version: str

    # Per-scenario results
    scenario_results: List[ScenarioEvaluationResult]

    # Overall metrics (macro-averaged)
    macro_entity_f1: float = Field(ge=0.0, le=1.0)
    macro_relationship_f1: float = Field(ge=0.0, le=1.0)
    macro_claim_f1: float = Field(ge=0.0, le=1.0)
    overall_benchmark_score: float = Field(ge=0.0, le=1.0)

    # Coverage metrics
    total_scenarios_evaluated: int
    total_artifacts_evaluated: int
    total_entities_ground_truth: int
    total_relationships_ground_truth: int
    total_claims_ground_truth: int

    # Execution metadata
    evaluation_started_at: str = Field(..., description="ISO 8601 timestamp")
    evaluation_completed_at: str = Field(..., description="ISO 8601 timestamp")
    duration_seconds: float

    # Errors encountered
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class EvaluationBatch(BaseModel):
    """Batch of evaluations for comparison/trending."""
    batch_id: str
    results: List[DatasetEvaluationResult]
    generated_at: str = Field(..., description="ISO 8601 timestamp")
