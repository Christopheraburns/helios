"""Deterministic scenario planning for Helios-DS.

A scenario is a synthetic business story selected from TPC-DS records and
rendered into one or more artifacts. Planning is deterministic:

1. **Allocation.** Each enabled artifact type's target count is split across the
   scenario types that can produce it, in proportion to scenario weights, with
   largest-remainder rounding. The planned total per artifact type therefore
   equals its target exactly whenever enough source records exist.
2. **Selection.** Every eligible record gets a stable ranking key,
   SHA256(master_seed, scenario_type, business_key, schema version); records are
   sorted by it and the top N kept. Source row order and database result order
   never affect the chosen population.
3. **Seeds and IDs.** Scenario and artifact seeds follow the spec's SHA-256
   chain; IDs are UUIDv5 values namespaced by the dataset.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Sequence, Tuple

from pydantic import BaseModel

from .config import DatasetConfig
from .ids import (
    GENERATOR_SCHEMA_VERSION,
    artifact_id,
    artifact_seed,
    dataset_seed,
    hash_parts,
    scenario_id,
    scenario_seed,
)
from .templates import Template, TemplateRegistry
from .tpcds import TpcdsRepository


@dataclass(frozen=True)
class SourceRef:
    """A TPC-DS row a scenario derives from: ``table`` keyed by record fields."""

    table: str
    key: Mapping[str, str]  # table column -> eligibility-record field


@dataclass(frozen=True)
class ScenarioDefinition:
    scenario_type: str
    description: str
    source_table: str
    key_columns: Tuple[str, ...]
    tables: Tuple[str, ...]
    eligibility_sql: str
    source_refs: Tuple[SourceRef, ...]
    artifacts: Mapping[str, str] = field(default_factory=dict)  # artifact_type -> template_id
    # One-line, human-readable story for UIs, filled from the facts (display
    # only: not part of the manifest or any ID).
    headline: str = ""

    def describe(self, facts: Mapping[str, Any]) -> str:
        class _Missing(dict):  # type: ignore[type-arg]
            def __missing__(self, key: str) -> str:
                return "?"

        return self.headline.format_map(_Missing({k: v for k, v in facts.items() if v is not None}))

    def business_key(self, record: Mapping[str, Any]) -> str:
        return f"{self.source_table}:" + "|".join(f"{c}={record[c]}" for c in self.key_columns)

    def refs_for(self, record: Mapping[str, Any]) -> List[Dict[str, Any]]:
        refs = []
        for ref in self.source_refs:
            key = {column: record[fld] for column, fld in ref.key.items()}
            if all(v is not None for v in key.values()):
                refs.append({"table": ref.table, "key": key})
        return refs


_DAMAGE_REASON = "lower(r.r_reason_desc) LIKE '%damaged%'"
_COMPLAINT_REASONS = " OR ".join(
    f"lower(r.r_reason_desc) LIKE '%{p}%'"
    for p in (
        "stopped working",
        "not working",
        "did not get it on time",
        "not the product",
        "parts missing",
        "does not work",
        "wrong size",
        "did not fit",
    )
)

SCENARIOS: Dict[str, ScenarioDefinition] = {
    d.scenario_type: d
    for d in (
        ScenarioDefinition(
            scenario_type="product_return_damage",
            description="Store return of a damaged product: report, email, chat, photo, call",
            source_table="store_returns",
            key_columns=("sr_ticket_number", "sr_item_sk"),
            tables=(
                "store_returns",
                "reason",
                "item",
                "customer",
                "store",
                "date_dim",
                "store_sales",
            ),
            eligibility_sql=f"""
                SELECT sr.sr_ticket_number, sr.sr_item_sk, sr.sr_customer_sk, sr.sr_store_sk,
                       sr.sr_reason_sk, sr.sr_return_quantity, sr.sr_return_amt,
                       rd.d_date AS return_date, r.r_reason_desc,
                       i.i_item_id, i.i_product_name, i.i_brand, i.i_category, i.i_class,
                       i.i_manufact, i.i_color, c.c_customer_id, c.c_salutation,
                       c.c_first_name, c.c_last_name,
                       c.c_email_address, s.s_store_id, s.s_store_name, s.s_city, s.s_state,
                       ss.ss_ticket_number, ss.ss_item_sk, sd.d_date AS sale_date,
                       ss.ss_sales_price, ss.ss_quantity, ss.ss_promo_sk
                FROM store_returns sr
                JOIN reason r ON r.r_reason_sk = sr.sr_reason_sk
                JOIN item i ON i.i_item_sk = sr.sr_item_sk
                JOIN customer c ON c.c_customer_sk = sr.sr_customer_sk
                JOIN store s ON s.s_store_sk = sr.sr_store_sk
                JOIN date_dim rd ON rd.d_date_sk = sr.sr_returned_date_sk
                LEFT JOIN store_sales ss
                  ON ss.ss_ticket_number = sr.sr_ticket_number AND ss.ss_item_sk = sr.sr_item_sk
                LEFT JOIN date_dim sd ON sd.d_date_sk = ss.ss_sold_date_sk
                WHERE {_DAMAGE_REASON}
            """,
            source_refs=(
                SourceRef(
                    "store_returns",
                    {"sr_ticket_number": "sr_ticket_number", "sr_item_sk": "sr_item_sk"},
                ),
                SourceRef(
                    "store_sales",
                    {"ss_ticket_number": "ss_ticket_number", "ss_item_sk": "ss_item_sk"},
                ),
                SourceRef("item", {"i_item_sk": "sr_item_sk"}),
                SourceRef("customer", {"c_customer_sk": "sr_customer_sk"}),
                SourceRef("store", {"s_store_sk": "sr_store_sk"}),
                SourceRef("reason", {"r_reason_sk": "sr_reason_sk"}),
            ),
            artifacts={
                "pdf": "return_report",
                "email": "damaged_item",
                "chat": "support_return",
                "image": "damage_photo",
                "audio": "support_call",
            },
            headline=(
                "{c_first_name} {c_last_name} returned {i_category} / {i_class} item "
                "{i_product_name} ({i_item_id}) to store {s_store_name}, {s_city} {s_state} "
                "on {return_date}: {r_reason_desc}"
            ),
        ),
        ScenarioDefinition(
            scenario_type="warehouse_inventory_issue",
            description="Low stock for an item at a warehouse: report, photo, video, ops chat",
            source_table="inventory",
            key_columns=("inv_date_sk", "inv_item_sk", "inv_warehouse_sk"),
            tables=("inventory", "warehouse", "item", "date_dim"),
            eligibility_sql="""
                SELECT inv.inv_date_sk, inv.inv_item_sk, inv.inv_warehouse_sk,
                       inv.inv_quantity_on_hand, d.d_date AS inventory_date,
                       w.w_warehouse_id, w.w_warehouse_name, w.w_city, w.w_state,
                       i.i_item_id, i.i_product_name, i.i_brand, i.i_category, i.i_class
                FROM inventory inv
                JOIN warehouse w ON w.w_warehouse_sk = inv.inv_warehouse_sk
                JOIN item i ON i.i_item_sk = inv.inv_item_sk
                JOIN date_dim d ON d.d_date_sk = inv.inv_date_sk
                WHERE inv.inv_quantity_on_hand IS NOT NULL AND inv.inv_quantity_on_hand < 5
            """,
            source_refs=(
                SourceRef(
                    "inventory",
                    {
                        "inv_date_sk": "inv_date_sk",
                        "inv_item_sk": "inv_item_sk",
                        "inv_warehouse_sk": "inv_warehouse_sk",
                    },
                ),
                SourceRef("warehouse", {"w_warehouse_sk": "inv_warehouse_sk"}),
                SourceRef("item", {"i_item_sk": "inv_item_sk"}),
            ),
            artifacts={
                "pdf": "warehouse_report",
                "image": "inspection_photo",
                "video": "warehouse_inspection",
                "chat": "warehouse_ops",
            },
            headline=(
                "{i_category} / {i_class} item {i_product_name} ({i_item_id}) low at "
                "warehouse {w_warehouse_name}, {w_city} {w_state}: {inv_quantity_on_hand} on hand "
                "on {inventory_date}"
            ),
        ),
        ScenarioDefinition(
            scenario_type="promotion_performance",
            description="Promotion of an item: campaign brief, email, image, marketing chat",
            source_table="promotion",
            key_columns=("p_promo_sk",),
            tables=("promotion", "item", "date_dim"),
            eligibility_sql="""
                SELECT p.p_promo_sk, p.p_promo_id, p.p_promo_name, p.p_cost, p.p_purpose,
                       p.p_channel_email, p.p_channel_tv, p.p_channel_radio,
                       sd.d_date AS start_date, ed.d_date AS end_date, p.p_item_sk,
                       i.i_item_id, i.i_product_name, i.i_brand, i.i_category
                FROM promotion p
                JOIN item i ON i.i_item_sk = p.p_item_sk
                JOIN date_dim sd ON sd.d_date_sk = p.p_start_date_sk
                JOIN date_dim ed ON ed.d_date_sk = p.p_end_date_sk
            """,
            source_refs=(
                SourceRef("promotion", {"p_promo_sk": "p_promo_sk"}),
                SourceRef("item", {"i_item_sk": "p_item_sk"}),
            ),
            artifacts={
                "pdf": "campaign_brief",
                "email": "campaign_email",
                "image": "campaign_image",
                "chat": "marketing_chat",
            },
            headline=(
                "Promotion {p_promo_name} ({p_promo_id}) for {i_category} item "
                "{i_product_name} ({i_item_id}), {start_date} to {end_date}"
            ),
        ),
        ScenarioDefinition(
            scenario_type="customer_complaint",
            description="Web return with a complaint reason: email, support chat, support call",
            source_table="web_returns",
            key_columns=("wr_order_number", "wr_item_sk"),
            tables=("web_returns", "reason", "item", "customer", "date_dim", "web_page"),
            eligibility_sql=f"""
                SELECT wr.wr_order_number, wr.wr_item_sk, wr.wr_returning_customer_sk,
                       wr.wr_web_page_sk, wr.wr_reason_sk, wr.wr_return_quantity,
                       wr.wr_return_amt, rd.d_date AS return_date, r.r_reason_desc,
                       i.i_item_id, i.i_product_name, i.i_brand, i.i_category,
                       c.c_customer_id, c.c_first_name, c.c_last_name, c.c_email_address,
                       wp.wp_web_page_id
                FROM web_returns wr
                JOIN reason r ON r.r_reason_sk = wr.wr_reason_sk
                JOIN item i ON i.i_item_sk = wr.wr_item_sk
                JOIN customer c ON c.c_customer_sk = wr.wr_returning_customer_sk
                JOIN date_dim rd ON rd.d_date_sk = wr.wr_returned_date_sk
                LEFT JOIN web_page wp ON wp.wp_web_page_sk = wr.wr_web_page_sk
                WHERE {_COMPLAINT_REASONS}
            """,
            source_refs=(
                SourceRef(
                    "web_returns",
                    {"wr_order_number": "wr_order_number", "wr_item_sk": "wr_item_sk"},
                ),
                SourceRef("item", {"i_item_sk": "wr_item_sk"}),
                SourceRef("customer", {"c_customer_sk": "wr_returning_customer_sk"}),
                SourceRef("reason", {"r_reason_sk": "wr_reason_sk"}),
                SourceRef("web_page", {"wp_web_page_sk": "wr_web_page_sk"}),
            ),
            artifacts={
                "email": "complaint_email",
                "chat": "support_chat",
                "audio": "support_call",
            },
            headline=(
                "{c_first_name} {c_last_name} returned {i_category} item {i_product_name} "
                "({i_item_id}) from web order {wr_order_number} on {return_date}: {r_reason_desc}"
            ),
        ),
    )
}


class ArtifactPlan(BaseModel):
    artifact_id: str
    artifact_type: str
    ordinal: int
    template_id: str
    template_version: str
    artifact_seed: str


class ScenarioPlan(BaseModel):
    scenario_id: str
    scenario_type: str
    business_key: str
    rank_score: str
    scenario_seed: str
    source_refs: List[Dict[str, Any]]
    facts: Dict[str, Any]
    artifacts: List[ArtifactPlan]


class GenerationPlan(BaseModel):
    scenarios: List[ScenarioPlan]
    artifact_counts: Dict[str, Dict[str, int]]  # type -> {target, planned}
    scenario_counts: Dict[str, Dict[str, int]]  # type -> {requested, eligible, planned}


def candidate_score(master_seed: int, scenario_type: str, business_key: str) -> str:
    return hash_parts(master_seed, scenario_type, business_key, GENERATOR_SCHEMA_VERSION)


def rank_candidates(
    records: Sequence[Mapping[str, Any]],
    master_seed: int,
    scenario_type: str,
    business_key: Callable[[Mapping[str, Any]], str],
) -> List[Tuple[str, str, Mapping[str, Any]]]:
    """(score, business_key, record) sorted by score. Duplicate keys are an error."""
    ranked = []
    seen = set()
    for record in records:
        key = business_key(record)
        if key in seen:
            raise ValueError(f"{scenario_type}: duplicate business key {key}")
        seen.add(key)
        ranked.append((candidate_score(master_seed, scenario_type, key), key, record))
    ranked.sort(key=lambda item: item[0])
    return ranked


def _largest_remainder(total: int, weights: Mapping[str, float]) -> Dict[str, int]:
    weight_sum = sum(weights.values())
    quotas = {name: total * w / weight_sum for name, w in weights.items()}
    counts = {name: int(q) for name, q in quotas.items()}
    leftover = total - sum(counts.values())
    by_remainder = sorted(quotas, key=lambda n: (-(quotas[n] - counts[n]), n))
    for name in by_remainder[:leftover]:
        counts[name] += 1
    return counts


def allocate(config: DatasetConfig) -> Tuple[Dict[str, Dict[str, int]], Dict[str, int]]:
    """Split artifact targets across scenario types.

    Returns ({scenario_type: {artifact_type: count}}, {artifact_type: unallocatable}).
    """
    weights = {name: w for name, w in config.normalized_scenario_weights().items() if w > 0}
    allocation: Dict[str, Dict[str, int]] = {name: {} for name in weights}
    unallocated: Dict[str, int] = {}
    for artifact_type in sorted(config.artifacts):
        artifact = config.artifacts[artifact_type]
        if not artifact.enabled or artifact.target_count == 0:
            continue
        supporters = {s: w for s, w in weights.items() if artifact_type in SCENARIOS[s].artifacts}
        if not supporters:
            unallocated[artifact_type] = artifact.target_count
            continue
        for scenario_type, count in _largest_remainder(artifact.target_count, supporters).items():
            if count:
                allocation[scenario_type][artifact_type] = count
    return allocation, unallocated


def tables_for(config: DatasetConfig) -> List[str]:
    """TPC-DS tables the enabled scenarios read (fingerprint scope)."""
    allocation, _ = allocate(config)
    return sorted({t for s, a in allocation.items() if a for t in SCENARIOS[s].tables})


class ScenarioPlanner:
    def __init__(self, config: DatasetConfig, templates: TemplateRegistry):
        self.config = config
        self.templates = templates

    def plan(self, repository: TpcdsRepository, dataset_id: str) -> GenerationPlan:
        allocation, unallocated = allocate(self.config)
        root_seed = dataset_seed(self.config.master_seed)
        scenarios: List[ScenarioPlan] = []
        scenario_counts: Dict[str, Dict[str, int]] = {}
        planned: Dict[str, int] = {}

        for scenario_type in sorted(allocation):
            per_type = allocation[scenario_type]
            requested = max(per_type.values(), default=0)
            if requested == 0:
                continue
            definition = SCENARIOS[scenario_type]
            ranked = rank_candidates(
                repository.query(definition.eligibility_sql),
                self.config.master_seed,
                scenario_type,
                definition.business_key,
            )
            chosen = ranked[:requested]
            scenario_counts[scenario_type] = {
                "requested": requested,
                "eligible": len(ranked),
                "planned": len(chosen),
            }
            for index, (score, key, record) in enumerate(chosen):
                s_seed = scenario_seed(root_seed, scenario_type, key)
                s_id = scenario_id(dataset_id, scenario_type, key)
                artifacts = []
                for artifact_type in sorted(per_type):
                    if index >= per_type[artifact_type]:
                        continue
                    template = self._template(definition, artifact_type)
                    artifacts.append(
                        ArtifactPlan(
                            artifact_id=artifact_id(
                                dataset_id, s_id, artifact_type, 0, template.template_version
                            ),
                            artifact_type=artifact_type,
                            ordinal=0,
                            template_id=template.template_id,
                            template_version=template.template_version,
                            artifact_seed=artifact_seed(
                                s_seed,
                                artifact_type,
                                0,
                                template.template_id,
                                template.template_version,
                            ),
                        )
                    )
                    planned[artifact_type] = planned.get(artifact_type, 0) + 1
                scenarios.append(
                    ScenarioPlan(
                        scenario_id=s_id,
                        scenario_type=scenario_type,
                        business_key=key,
                        rank_score=score,
                        scenario_seed=s_seed,
                        source_refs=definition.refs_for(record),
                        facts=dict(sorted(record.items())),
                        artifacts=artifacts,
                    )
                )

        artifact_counts = {
            t: {"target": a.target_count, "planned": planned.get(t, 0)}
            for t, a in sorted(self.config.artifacts.items())
            if a.enabled and a.target_count
        }
        for artifact_type, count in unallocated.items():
            artifact_counts[artifact_type] = {"target": count, "planned": 0}
        return GenerationPlan(
            scenarios=scenarios, artifact_counts=artifact_counts, scenario_counts=scenario_counts
        )

    def _template(self, definition: ScenarioDefinition, artifact_type: str) -> Template:
        template = self.templates.get(definition.artifacts[artifact_type])
        spec = template.spec
        if (
            spec.artifact_type != artifact_type
            or definition.scenario_type not in spec.supported_scenario_types
        ):
            raise ValueError(
                f"template {spec.template_id} does not render {artifact_type} "
                f"for {definition.scenario_type}"
            )
        return template


def planned_artifact_count(plan: GenerationPlan) -> int:
    return sum(len(s.artifacts) for s in plan.scenarios)


def expected_scenario_columns(scenario_type: str, repository: TpcdsRepository) -> List[str]:
    """Column names the eligibility SQL returns (used to check template fields)."""
    return repository.columns(SCENARIOS[scenario_type].eligibility_sql)
