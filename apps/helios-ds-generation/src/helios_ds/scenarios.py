"""Deterministic scenario planning for Helios-DS.

The scenario planner selects TPC-DS records to become test scenarios without
relying on database result order or shared RNG state. For every eligible record,
compute a stable ranking key, sort, and select the top N.
"""
import hashlib
from dataclasses import dataclass
from typing import List, Optional

from .ids import DeterministicIDGenerator


@dataclass
class Scenario:
    """Represents a scenario plan derived from TPC-DS data."""
    scenario_id: str
    scenario_type: str
    source_key: dict
    weight: float
    artifacts_plan: List[dict]  # Planned artifacts for this scenario


class ScenarioPlanner:
    """Deterministic scenario selection from TPC-DS records."""

    # Scenario types and their artifact templates
    SCENARIO_TEMPLATES = {
        "product_return_damage": {
            "artifacts": ["pdf", "email", "image"],
            "description": "Product returned with damage; sales records, emails, photos",
        },
        "warehouse_inventory_issue": {
            "artifacts": ["pdf", "image", "video"],
            "description": "Warehouse stock discrepancy; reports, photos, videos",
        },
        "promotion_performance": {
            "artifacts": ["pdf", "email", "chat"],
            "description": "Promotion results; campaign briefs, emails, chat threads",
        },
        "customer_complaint": {
            "artifacts": ["email", "audio", "chat"],
            "description": "Customer complaint; complaint email, support call, chat thread",
        },
    }

    def __init__(self, master_seed: int, generator_version: str):
        """Initialize planner.

        Args:
            master_seed: Seed for deterministic selection
            generator_version: Version tag for reproducibility
        """
        self.master_seed = master_seed
        self.generator_version = generator_version

    def compute_candidate_score(
        self,
        master_seed: int,
        scenario_type: str,
        canonical_tpcds_business_key: str,
        generator_schema_version: str,
    ) -> str:
        """Compute stable ranking key for a candidate.

        Same seed + scenario_type + key + version always produces same score.
        Candidates are sorted by score and top N are selected.
        This means reordering source rows does not change the chosen population.

        Args:
            master_seed: Generation master seed
            scenario_type: Type of scenario
            canonical_tpcds_business_key: Canonical TPC-DS business key
            generator_schema_version: Schema version for reproducibility

        Returns:
            Hex-encoded SHA256 hash (used for sorting)
        """
        preimage = f"{master_seed}|{scenario_type}|{canonical_tpcds_business_key}|{generator_schema_version}"
        return hashlib.sha256(preimage.encode()).hexdigest()

    def select_candidates(
        self,
        all_records: List[dict],
        scenario_type: str,
        target_count: int,
        weight: float,
    ) -> List[dict]:
        """Select top N records by stable ranking.

        Args:
            all_records: All eligible TPC-DS records (in any order)
            scenario_type: Scenario type to select for
            target_count: How many to select
            weight: Weight of this scenario in overall generation

        Returns:
            Selected records, deterministically chosen
        """
        # Score each record
        scored = []
        for record in all_records:
            # Extract canonical key (e.g., from sale_id, item_id)
            key = str(record.get("business_key", record))

            score = self.compute_candidate_score(
                self.master_seed,
                scenario_type,
                key,
                "1.0",  # Schema version for reproducibility
            )
            scored.append({
                "record": record,
                "score": score,
                "key": key,
            })

        # Sort by score (deterministic)
        scored.sort(key=lambda x: x["score"])

        # Take top N
        selected = scored[:target_count]
        return [item["record"] for item in selected]

    def plan_scenarios(
        self,
        tpcds_records: dict,
        scenario_config: dict,
        difficulty_config: dict,
    ) -> List[Scenario]:
        """Generate scenario plans from TPC-DS records.

        Args:
            tpcds_records: Grouped TPC-DS records by entity type
            scenario_config: {scenario_type: {weight: float}}
            difficulty_config: Difficulty distribution

        Returns:
            List of planned scenarios
        """
        scenarios = []

        for scenario_type, config in scenario_config.items():
            if scenario_type not in self.SCENARIO_TEMPLATES:
                continue

            template = self.SCENARIO_TEMPLATES[scenario_type]
            weight = config.get("weight", 1.0)

            # For now, create a basic scenario plan
            # In full implementation, would select actual TPC-DS records
            scenario = Scenario(
                scenario_id=f"scenario_{scenario_type}",  # Placeholder
                scenario_type=scenario_type,
                source_key={"placeholder": "key"},  # Placeholder
                weight=weight,
                artifacts_plan=[
                    {"type": artifact_type, "ordinal": 0}
                    for artifact_type in template["artifacts"]
                ],
            )
            scenarios.append(scenario)

        return scenarios
