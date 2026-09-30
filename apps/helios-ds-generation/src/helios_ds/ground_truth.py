"""Hidden ground truth (helios_ground_truth.*) built from rendered artifacts (task C-04).

The renderers place every entity mention and claim-supporting passage themselves
and record exact locators. This module
1. verifies each locator against the artifact's actual bytes (the located text
   must be exactly the recorded surface form or excerpt), and
2. turns them into entities, mentions, relationships, claims and evidence rows
   with deterministic IDs.

Locators by artifact type:
- email: {"part": "body"|"subject", "start", "end"} (body offsets use "\\n"
  line endings), or {"header": "From"} for the sender's display name;
- chat:  {"message_id", "start", "end"} within that message's text;
- pdf:   {"page", "text"}: the exact text appears on that page (whitespace-
  normalised, since paragraphs wrap).
"""

import email
import email.policy
import io
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .ids import claim_id, truth_entity_id, truth_row_id
from .render.base import RenderedArtifact
from .render.stories import ENTITY_TYPES, Story
from .scenarios import ArtifactPlan, ScenarioPlan
from .schemas import (
    ArtifactRecord,
    TruthClaimRecord,
    TruthEntityMentionRecord,
    TruthEntityRecord,
    TruthEvidenceRecord,
    TruthRelationshipRecord,
)

TRUTH_TABLES = (
    "helios_ground_truth.entities",
    "helios_ground_truth.entity_mentions",
    "helios_ground_truth.relationships",
    "helios_ground_truth.claims",
    "helios_ground_truth.evidence",
)


class LocatorError(ValueError):
    """A recorded locator does not resolve to the recorded text."""


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class ArtifactText:
    """Resolves locators against an artifact's bytes (parsed once)."""

    def __init__(self, mime_type: str, data: bytes):
        self.mime_type = mime_type
        self._email: Any = None
        self._body = ""
        self._messages: Dict[str, str] = {}
        self._pages: Optional[List[str]] = None
        if mime_type == "message/rfc822":
            self._email = email.message_from_bytes(data, policy=email.policy.default)
            part = self._email.get_body(preferencelist=("plain",))
            self._body = part.get_content().replace("\r\n", "\n") if part is not None else ""
        elif mime_type == "application/json":
            thread = json.loads(data)
            self._messages = {m["message_id"]: m["text"] for m in thread.get("messages", [])}
        elif mime_type == "application/pdf":
            self._data = data

    def pdf_pages(self) -> Optional[List[str]]:
        """Squashed page texts, or None when pypdf is not installed."""
        if self._pages is None:
            try:
                from pypdf import PdfReader
            except ImportError:
                return None
            self._pages = [
                _squash(p.extract_text() or "") for p in PdfReader(io.BytesIO(self._data)).pages
            ]
        return self._pages

    def check(self, locator: Dict[str, Any], expected: str) -> Optional[bool]:
        """True if the locator resolves to ``expected``; None if it can't be checked here."""
        if self.mime_type == "message/rfc822":
            if locator.get("header") == "From":
                addresses = self._email["From"].addresses
                return bool(addresses) and addresses[0].display_name == expected
            text = self._body if locator.get("part") == "body" else str(self._email["Subject"])
            return text[locator["start"] : locator["end"]] == expected
        if self.mime_type == "application/json":
            message = self._messages.get(locator.get("message_id", ""))
            return message is not None and message[locator["start"] : locator["end"]] == expected
        if self.mime_type == "application/pdf":
            pages = self.pdf_pages()
            if pages is None:
                return None
            page = int(locator.get("page", 0))
            return 1 <= page <= len(pages) and _squash(expected) in pages[page - 1]
        return None


@dataclass
class VerificationResult:
    checked: int = 0
    unchecked: int = 0  # e.g. PDF locators when pypdf is unavailable


def verify_locators(artifact_id: str, out: RenderedArtifact) -> VerificationResult:
    """Raise LocatorError unless every mention and evidence locator resolves exactly."""
    text = ArtifactText(out.mime_type, out.data)
    result = VerificationResult()
    items: List[Tuple[str, Dict[str, Any]]] = [(m.surface_form, m.locator) for m in out.mentions]
    items += [(e.excerpt, e.locator) for e in out.evidence]
    for expected, locator in items:
        ok = text.check(locator, expected)
        if ok is None:
            result.unchecked += 1
        elif ok:
            result.checked += 1
        else:
            raise LocatorError(
                f"artifact {artifact_id}: locator {locator} does not resolve to {expected!r}"
            )
    return result


@dataclass
class GroundTruth:
    entities: Dict[str, TruthEntityRecord] = field(default_factory=dict)
    mentions: List[TruthEntityMentionRecord] = field(default_factory=list)
    relationships: Dict[str, TruthRelationshipRecord] = field(default_factory=dict)
    claims: Dict[str, TruthClaimRecord] = field(default_factory=dict)
    evidence: List[TruthEvidenceRecord] = field(default_factory=list)

    def rows(self) -> Dict[str, List[Any]]:
        """Rows per table, sorted by ID so writes are deterministic."""
        return {
            "helios_ground_truth.entities": sorted(
                self.entities.values(), key=lambda r: r.entity_id
            ),
            "helios_ground_truth.entity_mentions": sorted(
                self.mentions, key=lambda r: r.mention_id
            ),
            "helios_ground_truth.relationships": sorted(
                self.relationships.values(), key=lambda r: r.relationship_id
            ),
            "helios_ground_truth.claims": sorted(self.claims.values(), key=lambda r: r.claim_id),
            "helios_ground_truth.evidence": sorted(self.evidence, key=lambda r: r.evidence_id),
        }

    def counts(self) -> Dict[str, int]:
        return {table.split(".")[1]: len(rows) for table, rows in self.rows().items()}


def _key_json(key: Dict[str, Any]) -> str:
    return json.dumps(key, sort_keys=True, separators=(",", ":"))


class GroundTruthBuilder:
    def __init__(self, dataset_id: str):
        self.dataset_id = dataset_id
        self.truth = GroundTruth()

    def _entity(self, entity_type: str, key: Dict[str, Any], canonical_name: str) -> str:
        entity_id = truth_entity_id(self.dataset_id, entity_type, _key_json(key))
        if entity_id not in self.truth.entities:
            self.truth.entities[entity_id] = TruthEntityRecord(
                dataset_id=self.dataset_id,
                entity_id=entity_id,
                entity_type=entity_type,
                source_key=key,
                canonical_name=canonical_name,
            )
        return entity_id

    def _edge(
        self,
        source: str,
        predicate: str,
        target: str,
        scenario_id: str,
        artifact_id: Optional[str] = None,
    ) -> None:
        relationship_id = truth_row_id(self.dataset_id, "relationship", source, predicate, target)
        self.truth.relationships.setdefault(
            relationship_id,
            TruthRelationshipRecord(
                dataset_id=self.dataset_id,
                relationship_id=relationship_id,
                source_entity_id=source,
                predicate=predicate,
                target_entity_id=target,
                confidence=1.0,
                scenario_id=scenario_id,
                artifact_id=artifact_id,
            ),
        )

    def _scenario_entities(self, scenario: ScenarioPlan, story: Story) -> Dict[str, str]:
        """TPC-DS table -> entity_id for the scenario's source rows."""
        ids: Dict[str, str] = {}
        for ref in scenario.source_refs:
            table = ref["table"]
            key = {"table": table, **ref["key"]}
            entity_type = ENTITY_TYPES.get(table, table)
            ids[table] = self._entity(
                entity_type, key, story.canonical_names.get(table, _key_json(key))
            )
        for source, predicate, target in story.relationships:
            if source in ids and target in ids:
                self._edge(ids[source], predicate, ids[target], scenario.scenario_id)
        return ids

    def add_scenario(
        self,
        scenario: ScenarioPlan,
        story: Story,
        rendered: Sequence[Tuple[ArtifactPlan, RenderedArtifact, ArtifactRecord, str]],
    ) -> None:
        """Add one scenario's truth. ``rendered`` holds (plan, output, record, locator strategy)."""
        if not rendered:
            return
        entity_ids = self._scenario_entities(scenario, story)
        evidenced: Dict[str, List[Tuple[int, ArtifactPlan, Any, str]]] = {}
        for plan, out, _record, strategy in rendered:
            artifact_entity = self._entity(
                "Artifact",
                {"artifact_id": plan.artifact_id},
                f"{plan.artifact_type} {plan.template_id} {plan.artifact_id}",
            )
            if story.primary_table in entity_ids:
                self._edge(
                    artifact_entity,
                    "DISCUSSES",
                    entity_ids[story.primary_table],
                    scenario.scenario_id,
                    plan.artifact_id,
                )
            for index, mention in enumerate(out.mentions):
                key = mention.source_key
                table = key.get("table", "")
                canonical = (
                    mention.surface_form
                    if mention.entity_type == "Brand"
                    else story.canonical_names.get(table, _key_json(key))
                )
                entity_id = self._entity(mention.entity_type, key, canonical)
                self.truth.mentions.append(
                    TruthEntityMentionRecord(
                        dataset_id=self.dataset_id,
                        mention_id=truth_row_id(
                            self.dataset_id, "mention", plan.artifact_id, index
                        ),
                        entity_id=entity_id,
                        artifact_id=plan.artifact_id,
                        surface_form=mention.surface_form,
                        modality="text",
                        start_offset=mention.locator.get("start"),
                        end_offset=mention.locator.get("end"),
                        scenario_id=scenario.scenario_id,
                        entity_type=mention.entity_type,
                        locator=mention.locator,
                        difficulty=mention.tier,
                    )
                )
                self._edge(
                    artifact_entity, "MENTIONS", entity_id, scenario.scenario_id, plan.artifact_id
                )
            for index, item in enumerate(out.evidence):
                evidenced.setdefault(item.claim_type, []).append((index, plan, item, strategy))

        for claim_type, items in sorted(evidenced.items()):
            spec = story.claims.get(claim_type)
            if spec is None:
                raise ValueError(
                    f"{scenario.scenario_type}: evidence for undeclared claim {claim_type}"
                )
            subject = entity_ids.get(spec.subject_table)
            obj = entity_ids.get(spec.object_table)
            if subject is None or obj is None:
                continue  # the story lacks a source row this claim is about
            cid = claim_id(self.dataset_id, scenario.scenario_id, claim_type, subject, obj, 0)
            self.truth.claims[cid] = TruthClaimRecord(
                dataset_id=self.dataset_id,
                claim_id=cid,
                scenario_id=scenario.scenario_id,
                claim_type=claim_type,
                subject=subject,
                obj=obj,
                truth_status="INTENDED_TRUE",
                statement=story.statement(claim_type),
            )
            for index, plan, item, strategy in items:
                locator = item.locator
                segment = (
                    locator.get("message_id")
                    or locator.get("part")
                    or (f"page-{locator['page']}" if "page" in locator else None)
                )
                self.truth.evidence.append(
                    TruthEvidenceRecord(
                        dataset_id=self.dataset_id,
                        evidence_id=truth_row_id(
                            self.dataset_id, "evidence", plan.artifact_id, claim_type, index
                        ),
                        claim_id=cid,
                        artifact_id=plan.artifact_id,
                        segment_id=segment,
                        start_offset=locator.get("start"),
                        end_offset=locator.get("end"),
                        locator_type=strategy,
                        scenario_id=scenario.scenario_id,
                        locator=locator,
                        excerpt=item.excerpt,
                    )
                )
