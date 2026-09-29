"""Deterministic ID generation for Helios-DS artifacts."""
import hashlib
import uuid
from typing import Optional


HELIOS_DS_NAMESPACE = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")


def deterministic_hash(data: str, algorithm: str = "sha256") -> str:
    """Generate a stable hash for deterministic operations.

    Args:
        data: String to hash
        algorithm: Hash algorithm to use (default: sha256)

    Returns:
        Hex-encoded hash string
    """
    h = hashlib.new(algorithm)
    h.update(data.encode("utf-8"))
    return h.hexdigest()


def dataset_id(config_hash: str) -> str:
    """Generate stable dataset ID from config hash.

    Args:
        config_hash: SHA256 hash of canonical generation config

    Returns:
        UUID v5 string
    """
    return str(uuid.uuid5(HELIOS_DS_NAMESPACE, f"dataset:{config_hash}"))


def scenario_id(dataset_id: str, scenario_type: str, source_key: str) -> str:
    """Generate stable scenario ID.

    Args:
        dataset_id: Parent dataset ID
        scenario_type: Type of scenario (e.g., "product_return_damage")
        source_key: Canonical TPC-DS source key

    Returns:
        UUID v5 string
    """
    namespace = uuid.UUID(dataset_id)
    return str(uuid.uuid5(namespace, f"{scenario_type}:{source_key}"))


def artifact_id(
    scenario_id: str,
    artifact_type: str,
    ordinal: int,
    template_version: str,
) -> str:
    """Generate stable artifact ID.

    Args:
        scenario_id: Parent scenario ID
        artifact_type: Type of artifact (pdf, email, image, etc.)
        ordinal: Position in sequence
        template_version: Version of template used

    Returns:
        UUID v5 string
    """
    namespace = uuid.UUID(scenario_id)
    key = f"{artifact_type}:{ordinal}:v{template_version}"
    return str(uuid.uuid5(namespace, key))


def truth_entity_id(entity_type: str, source_key: str) -> str:
    """Generate stable entity ID for ground truth.

    Args:
        entity_type: Type of entity (Item, Customer, Sale, etc.)
        source_key: Canonical TPC-DS source key

    Returns:
        UUID v5 string
    """
    return str(uuid.uuid5(HELIOS_DS_NAMESPACE, f"entity:{entity_type}:{source_key}"))


def claim_id(
    scenario_id: str,
    claim_type: str,
    subject: str,
    obj: str,
    ordinal: int = 0,
) -> str:
    """Generate stable claim ID.

    Args:
        scenario_id: Parent scenario ID
        claim_type: Type of claim (e.g., PACKAGING_DAMAGED)
        subject: Subject entity reference
        obj: Object entity reference
        ordinal: Sequential position if multiple claims

    Returns:
        UUID v5 string
    """
    namespace = uuid.UUID(scenario_id)
    key = f"{claim_type}:{subject}:{obj}:{ordinal}"
    return str(uuid.uuid5(namespace, key))


def generation_job_id() -> str:
    """Generate random job ID for operational tracking.

    Job IDs are NOT deterministic - they distinguish operational attempts,
    not semantic content. Multiple runs with same dataset_id get different job_ids.

    Returns:
        Random UUID v4 string
    """
    return str(uuid.uuid4())


class DeterministicIDGenerator:
    """Coordinated deterministic ID generation for a dataset."""

    def __init__(self, dataset_id: str):
        """Initialize with dataset context.

        Args:
            dataset_id: UUID of the dataset
        """
        self.dataset_id = dataset_id
        self.dataset_namespace = uuid.UUID(dataset_id)

    def scenario(self, scenario_type: str, source_key: str) -> str:
        """Generate scenario ID."""
        return scenario_id(self.dataset_id, scenario_type, source_key)

    def artifact(
        self,
        scenario_id: str,
        artifact_type: str,
        ordinal: int,
        template_version: str,
    ) -> str:
        """Generate artifact ID."""
        return artifact_id(scenario_id, artifact_type, ordinal, template_version)

    def claim(
        self,
        scenario_id: str,
        claim_type: str,
        subject: str,
        obj: str,
        ordinal: int = 0,
    ) -> str:
        """Generate claim ID."""
        return claim_id(scenario_id, claim_type, subject, obj, ordinal)
