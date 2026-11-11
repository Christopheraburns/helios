"""Proposals: a discovery run's draft model, typed, with the reviewer's decisions on it.

``Proposer.propose`` returns one; a person (or a rule) accepts, edits or rejects its
elements; ``Publisher.publish`` publishes what was accepted. Underneath it is the same
two documents a run has always kept: the proposal (``propose.json``, never changed by
review) and the review (``review.json``, the decisions by element id), so the console,
the jobs and the graph read what the facades write.

Every element is a :class:`Proposal` with a confidence (0-1) and a rationale (None when
the LLM gave none), plus the fields of its kind. Elements are read-only snapshots: take
them again after a decision to see it.
"""
from __future__ import annotations

import copy
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any

from helios_model import review as review_store

PENDING = "pending"


@dataclass(frozen=True, kw_only=True)
class Proposal:
    """One proposed element of the model.

    ``section`` and ``id`` name it in the review (``review.SECTIONS`` and its element
    ids). ``decision`` is ``accept``, ``edit``, ``reject`` or ``pending``; a relationship
    the LLM turned down counts as rejected until a reviewer overturns it. ``element`` is
    the element as proposed, before any edit; ``overrides`` are the reviewer's edits."""

    section: str
    id: str
    label: str
    confidence: float
    rationale: str | None
    decision: str
    note: str | None = None
    overrides: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)
    element: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True, kw_only=True)
class ProposedField(Proposal):
    table: str
    column: str
    type: str | None
    role: str
    description: str
    refers_to: str | None


@dataclass(frozen=True, kw_only=True)
class ProposedDataset(Proposal):
    table: str
    kind: str
    description: str
    primary_key: tuple[str, ...]
    fields: tuple[ProposedField, ...]


@dataclass(frozen=True, kw_only=True)
class ProposedRelationship(Proposal):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    source: str | None          # profile, llm_confirmed or llm_rejected
    match_ratio: float | None


@dataclass(frozen=True, kw_only=True)
class ProposedMetric(Proposal):
    dataset: str
    expression: str
    description: str
    source: str | None          # query_history or derived


@dataclass(frozen=True, kw_only=True)
class ProposedTerm(Proposal):
    definition: str
    columns: tuple[str, ...]


def _frozen(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(copy.deepcopy(dict(value or {})))


def _confidence(element: Mapping[str, Any]) -> float:
    try:
        return float(element.get("confidence", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _rationale(element: Mapping[str, Any]) -> str | None:
    # Proposals written before rationale existed carry the LLM's relationship "reason" only.
    for key in ("rationale", "reason"):
        value = element.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class Proposals:
    """A run's proposal and the decisions on it.

    ``Proposals(proposal, run_id=..., model_id=...)`` starts with no decisions;
    ``review=`` continues a review (``Proposer.load`` reads both from a run)."""

    def __init__(self, proposal: Mapping[str, Any], *, run_id: str, model_id: str,
                 review: Mapping[str, Any] | None = None):
        for section in ("datasets", "relationships", "metrics", "glossary_terms"):
            if not isinstance(proposal.get(section, []), list):
                raise ValueError(f"proposal {section} must be a list")
        self.run_id = run_id
        self.model_id = model_id
        self._proposal = copy.deepcopy(dict(proposal))
        for section in ("datasets", "relationships", "metrics", "glossary_terms"):
            self._proposal.setdefault(section, [])
        self._review = copy.deepcopy(dict(review)) if review is not None else review_store.empty(run_id)
        for section in review_store.SECTIONS:
            self._review.setdefault(section, {})

    def __repr__(self) -> str:
        counts = {s: len(self._proposal[s]) for s in ("datasets", "relationships", "metrics", "glossary_terms")}
        return f"Proposals(run_id={self.run_id!r}, model_id={self.model_id!r}, {counts})"

    # ------------------------------------------------------------ documents
    @property
    def proposal(self) -> dict[str, Any]:
        """The proposal document, as proposed (a copy)."""
        return copy.deepcopy(self._proposal)

    @property
    def review(self) -> dict[str, Any]:
        """The review document: the decisions by element id (a copy)."""
        return copy.deepcopy(self._review)

    @property
    def llm(self) -> Mapping[str, Any]:
        """Which provider and model proposed it, and how many calls it took."""
        return _frozen(self._proposal.get("llm"))

    def reviewed(self) -> dict[str, Any]:
        """The proposal with only the accepted and edited elements, edits applied: what
        publishing builds the model from."""
        return review_store.apply(self._proposal, self._review)

    # ------------------------------------------------------------ elements
    def _decided(self, section: str, element_id: str, element: Mapping[str, Any]) -> dict[str, Any]:
        entry = self._review.get(section, {}).get(element_id) or {}
        return {"section": section, "id": element_id, "confidence": _confidence(element),
                "rationale": _rationale(element),
                "decision": review_store.effective_decision(self._review, section, element_id, dict(element)),
                "note": entry.get("note") or None, "overrides": _frozen(entry.get("overrides")),
                "element": _frozen(element)}

    def _field(self, dataset: Mapping[str, Any], f: Mapping[str, Any]) -> ProposedField:
        return ProposedField(**self._decided("fields", review_store.field_id(dataset, f), f),
                             label=f.get("name") or f["column"], table=dataset["table"], column=f["column"],
                             type=f.get("type"), role=f.get("role") or "attribute",
                             description=f.get("description") or "", refers_to=f.get("refers_to"))

    @property
    def datasets(self) -> tuple[ProposedDataset, ...]:
        return tuple(
            ProposedDataset(**self._decided("datasets", review_store.dataset_id(d), d),
                            label=d.get("name") or d["table"], table=d["table"], kind=d.get("kind") or "other",
                            description=d.get("description") or "", primary_key=tuple(d.get("primary_key") or ()),
                            fields=tuple(self._field(d, f) for f in d.get("fields", [])))
            for d in self._proposal["datasets"])

    @property
    def fields(self) -> tuple[ProposedField, ...]:
        return tuple(f for d in self.datasets for f in d.fields)

    @property
    def relationships(self) -> tuple[ProposedRelationship, ...]:
        return tuple(
            ProposedRelationship(**self._decided("relationships", review_store.relationship_id(r), r),
                                 label=f"{r['from']}.{r['from_column']} -> {r['to']}.{r['to_column']}",
                                 from_table=r["from"], from_column=r["from_column"], to_table=r["to"],
                                 to_column=r["to_column"], source=r.get("source"), match_ratio=r.get("match_ratio"))
            for r in self._proposal["relationships"])

    @property
    def metrics(self) -> tuple[ProposedMetric, ...]:
        return tuple(
            ProposedMetric(**self._decided("metrics", review_store.metric_id(m), m), label=m.get("name") or "",
                           dataset=m["dataset"], expression=m.get("expression") or "",
                           description=m.get("description") or "", source=m.get("source"))
            for m in self._proposal["metrics"])

    @property
    def glossary_terms(self) -> tuple[ProposedTerm, ...]:
        return tuple(
            ProposedTerm(**self._decided("glossary_terms", review_store.term_id(t), t), label=t["name"],
                         definition=t.get("definition") or "", columns=tuple(t.get("columns") or ()))
            for t in self._proposal["glossary_terms"])

    def __iter__(self) -> Iterator[Proposal]:
        """Every element: datasets, fields, relationships, metrics, then glossary terms."""
        yield from self.datasets
        yield from self.fields
        yield from self.relationships
        yield from self.metrics
        yield from self.glossary_terms

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def get(self, section: str, element_id: str) -> Proposal:
        """The element ``element_id`` in ``section``; KeyError when there is none."""
        if section not in review_store.SECTIONS:
            raise ValueError(f"unknown section {section!r}; expected one of {review_store.SECTIONS}")
        for element in getattr(self, section):
            if element.id == element_id:
                return element
        raise KeyError(f"no {section} element {element_id!r} in run {self.run_id}")

    def pending(self) -> tuple[Proposal, ...]:
        """The elements still waiting for a decision."""
        return tuple(e for e in self if e.decision == PENDING)

    def summary(self) -> dict[str, dict[str, int]]:
        """Decision counts per section (accept, edit, reject, pending, total)."""
        return review_store.summary(self._review, self._proposal)

    # ------------------------------------------------------------ decisions
    def _target(self, element: Proposal | tuple[str, str]) -> Proposal:
        section, element_id = (element.section, element.id) if isinstance(element, Proposal) else element
        return self.get(section, element_id)

    def accept(self, element: Proposal | tuple[str, str], *, note: str = "") -> None:
        target = self._target(element)
        review_store.decide(self._review, target.section, target.id, "accept", note=note)

    def reject(self, element: Proposal | tuple[str, str], *, note: str = "") -> None:
        target = self._target(element)
        review_store.decide(self._review, target.section, target.id, "reject", note=note)

    def edit(self, element: Proposal | tuple[str, str], changes: Mapping[str, Any], *, note: str = "") -> None:
        """Accept the element with ``changes`` merged over it (e.g. ``{"name": "Net paid"}``)."""
        if not changes:
            raise ValueError("an edit needs at least one change; use accept() to accept as proposed")
        target = self._target(element)
        review_store.decide(self._review, target.section, target.id, "edit", overrides=dict(changes), note=note)

    def accept_all(self, min_confidence: float = 0.0) -> int:
        """Accept every pending element whose confidence is at least ``min_confidence``,
        except relationships the LLM turned down. Returns how many were accepted."""
        return review_store.bulk_accept(self._review, self._proposal, min_confidence)

    def mark_reviewed(self, reviewed_by: str | None, at: datetime | None = None) -> None:
        """Record who reviewed the decisions and when (now, by default); a None reviewer
        keeps the one already recorded."""
        self._review["reviewed_at"] = (at or datetime.now(timezone.utc)).isoformat()
        self._review["reviewed_by"] = reviewed_by or self._review.get("reviewed_by")

    def clear(self, element: Proposal | tuple[str, str] | None = None) -> None:
        """Forget the decision on ``element``, or every decision."""
        if element is None:
            review_store.clear(self._review)
            return
        target = self._target(element)
        self._review[target.section].pop(target.id, None)
