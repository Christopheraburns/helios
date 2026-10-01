"""Golden questions (task C-08): the answer key for evaluating the crawler and Helios.

Generated with the dataset, after its ground truth, deterministically from the
manifest (TPC-DS facts), the ground truth and TPC-DS itself. Answers are
computed, never written by a model. Six kinds:

- structured:     the answer is a TPC-DS value (stored with the SQL that returns it)
- unstructured:   the answer is a claim, supported by evidence passages
- resolution:     asked by an alias that identifies one entity in the corpus
- joined:         needs the documents (to find the case) and TPC-DS (for the value)
- cross_document: the answer is built from claims across several artifacts
- no_answer:      a real TPC-DS item that no document discusses; the right
                  response is to say there is no evidence

Questions are asked as a single ``evaluator`` principal until simulated
access control (C-06) exists. Reviewers can curate them before approval (C-11).
"""

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .ground_truth import GroundTruth
from .ids import hash_parts, rng_for, truth_row_id
from .lakehouse import LakehouseSink
from .manifests import GenerationManifest
from .object_store import DeterminismIntegrityError
from .render.base import Case, money, pick
from .scenarios import ScenarioPlan
from .schemas import (
    ExpectedQueryRecord,
    ExpectedResultRecord,
    TruthClaimRecord,
    TruthEntityMentionRecord,
    TruthEvidenceRecord,
)
from .tpcds import TpcdsRepository

KINDS = ("structured", "unstructured", "resolution", "joined", "cross_document", "no_answer")
PER_KIND = 10
PRINCIPAL = "evaluator"
GOLDEN_TABLES = (
    "helios_ground_truth.expected_queries",
    "helios_ground_truth.expected_results",
)
SUPPORTED_STORIES = ("product_return_damage",)


@dataclass
class Golden:
    queries: List[ExpectedQueryRecord] = field(default_factory=list)
    results: List[ExpectedResultRecord] = field(default_factory=list)

    def rows(self) -> Dict[str, List[Any]]:
        return {
            "helios_ground_truth.expected_queries": sorted(self.queries, key=lambda r: r.query_id),
            "helios_ground_truth.expected_results": sorted(self.results, key=lambda r: r.result_id),
        }

    def counts(self) -> Dict[str, int]:
        by_kind: Dict[str, int] = defaultdict(int)
        for query in self.queries:
            by_kind[query.kind or "?"] += 1
        return dict(sorted(by_kind.items()))


@dataclass
class _Story:
    scenario: ScenarioPlan
    rma: str
    entities: Dict[str, str]  # entity type -> entity_id
    claims: Dict[str, TruthClaimRecord]  # claim type -> claim
    evidence: Dict[str, List[TruthEvidenceRecord]]  # claim_id -> evidence

    def fact(self, name: str) -> str:
        value = self.scenario.facts.get(name)
        return "" if value is None else str(value)

    @property
    def return_key(self) -> Dict[str, Any]:
        for ref in self.scenario.source_refs:
            if ref["table"] == "store_returns":
                return dict(ref["key"])
        return {}


class GoldenBuilder:
    def __init__(
        self,
        manifest: GenerationManifest,
        truth: GroundTruth,
        repository: Optional[TpcdsRepository] = None,
    ):
        self.dataset_id = manifest.dataset_id
        self.manifest = manifest
        self.truth = truth
        self.repository = repository
        self.golden = Golden()
        self.stories = self._stories()

    # --- indexes ---------------------------------------------------------------

    def _stories(self) -> List[_Story]:
        entities: Dict[str, Dict[str, str]] = defaultdict(dict)
        for m in self.truth.mentions:
            if m.scenario_id and m.entity_type not in (None, "Brand"):
                entities[m.scenario_id].setdefault(str(m.entity_type), m.entity_id)
        claims: Dict[str, Dict[str, TruthClaimRecord]] = defaultdict(dict)
        for claim in self.truth.claims.values():
            claims[claim.scenario_id][claim.claim_type] = claim
        evidence: Dict[str, List[TruthEvidenceRecord]] = defaultdict(list)
        for item in self.truth.evidence:
            evidence[item.claim_id].append(item)
        stories = []
        for scenario in self.manifest.scenarios:
            if scenario.scenario_type not in SUPPORTED_STORIES or not claims.get(
                scenario.scenario_id
            ):
                continue
            story_claims = claims[scenario.scenario_id]
            stories.append(
                _Story(
                    scenario,
                    Case.for_scenario(scenario).rma_number,
                    entities[scenario.scenario_id],
                    story_claims,
                    {
                        c.claim_id: sorted(evidence[c.claim_id], key=lambda e: e.evidence_id)
                        for c in story_claims.values()
                    },
                )
            )
        return stories

    def _choose(self, kind: str, items: Sequence[Any], key: Callable[[Any], str]) -> List[Any]:
        """Up to PER_KIND items, chosen by a stable hash (independent of input order)."""
        ranked = sorted(items, key=lambda i: hash_parts(self.dataset_id, "golden", kind, key(i)))
        return ranked[:PER_KIND]

    def _tiers_in(self, evidence: Iterable[TruthEvidenceRecord]) -> List[str]:
        """Tiers of the entity mentions that fall inside the evidence passages."""
        by_artifact: Dict[str, List[TruthEntityMentionRecord]] = defaultdict(list)
        for m in self.truth.mentions:
            by_artifact[m.artifact_id].append(m)
        tiers: Set[str] = set()
        for e in evidence:
            loc = e.locator
            for m in by_artifact[e.artifact_id]:
                ml = m.locator
                if "start" in loc and "start" in ml:
                    same_part = all(ml.get(k) == loc.get(k) for k in ("message_id", "part"))
                    inside = loc["start"] <= ml["start"] and ml["end"] <= loc["end"]
                    if same_part and inside and m.difficulty:
                        tiers.add(m.difficulty)
                elif "page" in loc and ml.get("page") == loc.get("page"):
                    if m.surface_form in (e.excerpt or "") and m.difficulty:
                        tiers.add(m.difficulty)
        return sorted(tiers)

    # --- recording -------------------------------------------------------------

    def _add(
        self,
        kind: str,
        anchor: str,
        wordings: Sequence[str],
        values: Dict[str, Any],
        *,
        result_type: str,
        answer: str,
        difficulty: str = "direct",
        entities: Iterable[str] = (),
        claims: Iterable[TruthClaimRecord] = (),
        evidence: Iterable[TruthEvidenceRecord] = (),
        artifacts: Iterable[str] = (),
        structured: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        rng = rng_for(hash_parts(self.dataset_id, "golden", kind, anchor))
        question = pick(rng, wordings).format_map(values)
        query_id = truth_row_id(self.dataset_id, "golden-query", kind, anchor)
        claims = sorted(claims, key=lambda c: c.claim_id)
        evidence = sorted(evidence, key=lambda e: e.evidence_id)
        self.golden.queries.append(
            ExpectedQueryRecord(
                dataset_id=self.dataset_id,
                query_id=query_id,
                question=question,
                principal_id=PRINCIPAL,
                required_structured=structured,
                required_entities=sorted(set(entities)),
                required_artifacts=sorted(set(artifacts) | {e.artifact_id for e in evidence}),
                required_claims=[c.claim_id for c in claims],
                kind=kind,
                difficulty=difficulty,
                required_evidence=[e.evidence_id for e in evidence],
            )
        )
        self.golden.results.append(
            ExpectedResultRecord(
                dataset_id=self.dataset_id,
                result_id=truth_row_id(self.dataset_id, "golden-result", query_id),
                query_id=query_id,
                result_type=result_type,
                result_data={
                    "answer": answer,
                    "abstain": result_type == "abstain",
                    "claims": [
                        {
                            "claim_id": c.claim_id,
                            "claim_type": c.claim_type,
                            "statement": c.statement,
                        }
                        for c in claims
                    ],
                    "evidence": [
                        {
                            "evidence_id": e.evidence_id,
                            "artifact_id": e.artifact_id,
                            "excerpt": e.excerpt,
                        }
                        for e in evidence
                    ],
                    "evidence_mention_tiers": self._tiers_in(evidence),
                    **(data or {}),
                },
            )
        )

    def _claim_evidence(
        self, story: _Story, *claim_types: str
    ) -> Tuple[List[TruthClaimRecord], List[TruthEvidenceRecord]]:
        claims = [story.claims[t] for t in claim_types if t in story.claims]
        return claims, [e for c in claims for e in story.evidence[c.claim_id]]

    # --- the six kinds ---------------------------------------------------------

    def structured(self) -> None:
        templates: List[Tuple[str, List[str], str, Callable[[Any], str]]] = [
            (
                "refund",
                [
                    "How much was refunded for the return of item {i_item_id} on ticket {ticket}?",
                    "What refund amount is recorded for item {i_item_id} returned on ticket "
                    "{ticket}?",
                ],
                "sr_return_amt",
                money,
            ),
            (
                "quantity",
                [
                    "How many units of {i_product_name} ({i_item_id}) were returned on ticket "
                    "{ticket}?",
                    "What quantity of item {i_item_id} came back on ticket {ticket}?",
                ],
                "sr_return_quantity",
                str,
            ),
        ]
        stories = [s for s in self.stories if s.return_key]
        for index, story in enumerate(
            self._choose("structured", stories, lambda s: s.scenario.scenario_id)
        ):
            name, wordings, column, display = templates[index % len(templates)]
            key = story.return_key
            value = story.scenario.facts.get(column)
            sql = (
                f"SELECT {column} FROM store_returns WHERE sr_ticket_number = "
                f"{key['sr_ticket_number']} AND sr_item_sk = {key['sr_item_sk']}"
            )
            self._add(
                "structured",
                f"{name}:{story.scenario.scenario_id}",
                wordings,
                {
                    "i_item_id": story.fact("i_item_id"),
                    "i_product_name": story.fact("i_product_name"),
                    "ticket": key["sr_ticket_number"],
                },
                result_type="value",
                answer=display(value),
                entities=[story.entities[t] for t in ("Return", "Item") if t in story.entities],
                structured=_sql_requirement("store_returns", key, sql, [[value]]),
                data={"value": value},
            )

    def unstructured(self) -> None:
        # A "why" is answered by a second claim: the refund was approved because
        # the packaging was damaged.
        templates = [
            (
                ("PACKAGING_DAMAGED",),
                [
                    "What did the returns desk find when it inspected return {rma}?",
                    "What was wrong with the merchandise on return {rma}?",
                ],
            ),
            (
                ("REFUND_APPROVED", "PACKAGING_DAMAGED"),
                [
                    "Was the refund on return {rma} approved, and why?",
                    "What was decided about the refund for return {rma}, and on what grounds?",
                ],
            ),
        ]
        chosen = self._choose("unstructured", self.stories, lambda s: s.scenario.scenario_id)
        for index, story in enumerate(chosen):
            claim_types, wordings = templates[index % len(templates)]
            if not all(t in story.claims for t in claim_types):
                continue
            claims, evidence = self._claim_evidence(story, *claim_types)
            self._add(
                "unstructured",
                f"{claim_types[0]}:{story.scenario.scenario_id}",
                wordings,
                {"rma": story.rma},
                result_type="claims",
                answer=" ".join(str(c.statement) for c in claims),
                entities=[e for c in claims for e in (c.subject, c.obj)],
                claims=claims,
                evidence=evidence,
            )

    def resolution(self) -> None:
        """Questions that name an entity only by an alias unique in the corpus."""
        entities_by_surface: Dict[str, Set[str]] = defaultdict(set)
        for m in self.truth.mentions:
            entities_by_surface[m.surface_form].add(m.entity_id)
        by_scenario = {s.scenario.scenario_id: s for s in self.stories}
        templates = {
            "Customer": (
                [
                    "What is {alias} contacting customer care about?",
                    "What problem did {alias} report?",
                ],
                ("REFUND_REQUESTED", "PACKAGING_DAMAGED"),
            ),
            "Sale": (
                [
                    "What was returned from the purchase on the {alias}, and why?",
                    "Why did the item bought on the {alias} come back?",
                ],
                ("PACKAGING_DAMAGED", "RETURN_REASON"),
            ),
            "Item": (
                [
                    "Why was the {alias} returned?",
                    "What happened to the {alias} that was brought back?",
                ],
                ("PACKAGING_DAMAGED", "RETURN_REASON"),
            ),
        }
        candidates: Dict[Tuple[str, str], Tuple[_Story, TruthEntityMentionRecord, List[str]]] = {}
        for m in self.truth.mentions:
            story = by_scenario.get(m.scenario_id or "")
            if (
                story is None
                or m.difficulty != "alias"
                or m.entity_type not in templates
                or len(entities_by_surface[m.surface_form]) != 1
            ):
                continue
            key = (story.scenario.scenario_id, m.surface_form)
            if key in candidates:
                candidates[key][2].append(m.artifact_id)
            else:
                candidates[key] = (story, m, [m.artifact_id])
        seen: Set[str] = set()
        for story, mention, artifacts in self._choose(
            "resolution",
            list(candidates.values()),
            lambda c: f"{c[0].scenario.scenario_id}|{c[1].surface_form}",
        ):
            if story.scenario.scenario_id in seen:
                continue  # one resolution question per story
            seen.add(story.scenario.scenario_id)
            wordings, claim_types = templates[str(mention.entity_type)]
            claims, evidence = self._claim_evidence(story, *claim_types)
            entity = self.truth.entities.get(mention.entity_id)
            self._add(
                "resolution",
                f"{story.scenario.scenario_id}|{mention.surface_form}",
                wordings,
                {"alias": mention.surface_form},
                result_type="claims",
                answer=" ".join(str(c.statement) for c in claims),
                difficulty="alias",
                entities=[mention.entity_id]
                + [c.subject for c in claims]
                + [c.obj for c in claims],
                claims=claims,
                evidence=evidence,
                artifacts=artifacts,
                data={
                    "alias": mention.surface_form,
                    "resolves_to": mention.entity_id,
                    "canonical_name": entity.canonical_name if entity else None,
                },
            )

    def joined(self) -> None:
        wordings = [
            "How much was refunded for return {rma} according to the store's records, and why "
            "was the refund approved?",
            "{customer_name} wrote in about a damaged {i_product_name}. What refund did the "
            "store record, and why was it approved?",
        ]
        stories = [
            s
            for s in self.stories
            if s.return_key and {"REFUND_APPROVED", "PACKAGING_DAMAGED"} <= set(s.claims)
        ]
        for story in self._choose("joined", stories, lambda s: s.scenario.scenario_id):
            key = story.return_key
            value = story.scenario.facts.get("sr_return_amt")
            claims, evidence = self._claim_evidence(story, "REFUND_APPROVED", "PACKAGING_DAMAGED")
            sql = (
                "SELECT sr_return_amt FROM store_returns WHERE sr_ticket_number = "
                f"{key['sr_ticket_number']} AND sr_item_sk = {key['sr_item_sk']}"
            )
            name = " ".join(p for p in (story.fact("c_first_name"), story.fact("c_last_name")) if p)
            self._add(
                "joined",
                story.scenario.scenario_id,
                wordings,
                {
                    "rma": story.rma,
                    "customer_name": name or story.fact("c_customer_id"),
                    "i_product_name": story.fact("i_product_name"),
                },
                result_type="value_and_claims",
                answer=f"{money(value)}. " + " ".join(str(c.statement) for c in claims),
                entities=[story.entities[t] for t in ("Return", "Customer") if t in story.entities],
                claims=claims,
                evidence=evidence,
                structured=_sql_requirement("store_returns", key, sql, [[value]]),
                data={"value": value},
            )

    def cross_document(self) -> None:
        damaged = [s for s in self.stories if "PACKAGING_DAMAGED" in s.claims]
        groups: List[Tuple[str, str, List[_Story]]] = []
        by_city: Dict[Tuple[str, str], List[_Story]] = defaultdict(list)
        by_brand: Dict[str, List[_Story]] = defaultdict(list)
        for s in damaged:
            if s.fact("s_city"):
                by_city[(s.fact("s_city"), s.fact("s_state"))].append(s)
            if s.fact("i_brand"):
                by_brand[s.fact("i_brand")].append(s)
        groups += [("city", f"{c}|{st}", v) for (c, st), v in by_city.items() if len(v) >= 2]
        groups += [("brand", b, v) for b, v in by_brand.items() if len(v) >= 2]
        for group, name, members in self._choose(
            "cross_document", groups, lambda g: f"{g[0]}|{g[1]}"
        ):
            members = sorted(members, key=lambda s: s.scenario.scenario_id)
            claims = [s.claims["PACKAGING_DAMAGED"] for s in members]
            evidence = [
                e for s in members for e in s.evidence[s.claims["PACKAGING_DAMAGED"].claim_id]
            ]
            items = sorted(
                {
                    (
                        f"{s.fact('i_product_name')} ({s.fact('i_item_id')})",
                        s.fact("s_store_name"),
                        s.rma,
                    )
                    for s in members
                }
            )
            listing = [{"item": i, "store": st, "rma": r} for i, st, r in items]
            if group == "city":
                city, state = name.split("|")
                wordings = [
                    "How many damaged-packaging returns at stores in {city}, {state} are "
                    "documented in the returns reports, emails and chats?",
                    "Across the documents, how many returns from {city}, {state} stores had "
                    "damaged packaging?",
                ]
                values = {"city": city, "state": state}
                result_type, answer = "count", str(len(members))
            else:
                wordings = [
                    "Which {brand} items came back with damaged packaging, and from which stores?",
                    "List the {brand} returns with packaging damage and the stores they were "
                    "returned to.",
                ]
                values = {"brand": name}
                result_type = "set"
                answer = "; ".join(f"{i['item']} at {i['store']} ({i['rma']})" for i in listing)
            self._add(
                "cross_document",
                f"{group}:{name}",
                wordings,
                values,
                result_type=result_type,
                answer=answer,
                entities=[
                    e
                    for s in members
                    for e in (s.entities.get("Item"), s.entities.get("Store"))
                    if e
                ],
                claims=claims,
                evidence=evidence,
                data={"group": group, "count": len(members), "items": listing},
            )

    def no_answer(self) -> None:
        if self.repository is None:
            return
        in_corpus_ids = {s.fact("i_item_id") for s in self.stories}
        in_corpus_names = {s.fact("i_product_name") for s in self.stories}
        rows = self.repository.query(
            "SELECT i_item_sk, i_item_id, i_product_name FROM item "
            "WHERE i_product_name IS NOT NULL AND i_item_id IS NOT NULL"
        )
        candidates = [
            r
            for r in rows
            if str(r["i_item_id"]) not in in_corpus_ids
            and str(r["i_product_name"]) not in in_corpus_names
        ]
        wordings = [
            "What have customers said about {i_product_name} ({i_item_id})?",
            "Were there any complaints or returns discussed for item {i_item_id} "
            "({i_product_name})?",
        ]
        for row in self._choose("no_answer", candidates, lambda r: str(r["i_item_sk"])):
            sql = f"SELECT i_item_id, i_product_name FROM item WHERE i_item_sk = {row['i_item_sk']}"
            self._add(
                "no_answer",
                str(row["i_item_sk"]),
                wordings,
                {"i_item_id": row["i_item_id"], "i_product_name": row["i_product_name"]},
                result_type="abstain",
                answer="No document discusses this item.",
                structured=_sql_requirement(
                    "item",
                    {"i_item_sk": row["i_item_sk"]},
                    sql,
                    [[row["i_item_id"], row["i_product_name"]]],
                ),
            )

    def build(self) -> Golden:
        for kind in KINDS:
            getattr(self, kind)()
        return self.golden


def _sql_requirement(
    table: str, key: Dict[str, Any], sql: str, rows: List[List[Any]]
) -> Dict[str, Any]:
    """What a correct answer must agree with in TPC-DS: the SQL, its rows and their hash."""
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str)
    return {
        "table": table,
        "key": key,
        "sql": sql,
        "rows": rows,
        "rows_sha256": hash_parts(canonical),
    }


def build_golden(
    manifest: GenerationManifest, truth: GroundTruth, repository: Optional[TpcdsRepository]
) -> Golden:
    return GoldenBuilder(manifest, truth, repository).build()


def write_golden(sink: LakehouseSink, dataset_id: str, golden: Golden) -> bool:
    """Write once; if rows exist but differ (a partial earlier attempt), replace them."""
    rows = golden.rows()
    ids = {
        "helios_ground_truth.expected_queries": "query_id",
        "helios_ground_truth.expected_results": "result_id",
    }
    existing = {
        t: sorted(getattr(r, ids[t]) for r in sink.read_dataset(t, dataset_id))
        for t in GOLDEN_TABLES
    }
    wanted = {t: [getattr(r, ids[t]) for r in rows[t]] for t in GOLDEN_TABLES}
    if existing == wanted:
        return False
    for table in GOLDEN_TABLES:
        if existing[table]:
            sink.delete_dataset_rows(table, dataset_id)
    for table in GOLDEN_TABLES:
        sink.append(table, rows[table])
    return True


def check_golden(sink: LakehouseSink, dataset_id: str, golden: Golden) -> None:
    """For an already-published dataset: stored questions must equal the regenerated
    ones. A dataset published before C-08 has none, and none are added."""

    def canonical(records: Iterable[Any]) -> List[str]:
        return sorted(
            json.dumps(r.model_dump(mode="json"), sort_keys=True, default=str) for r in records
        )

    stored = canonical(sink.read_dataset(GOLDEN_TABLES[0], dataset_id))
    if stored and stored != canonical(golden.queries):
        raise DeterminismIntegrityError(
            f"{dataset_id}: regenerated golden questions differ from the published ones"
        )
