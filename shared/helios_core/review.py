"""
Review: human decisions layered over a proposal.

propose.json is immutable evidence tied to a run. review.json (same directory) records what the
reviewer decided about each element:

    {"decision": "accept" | "reject" | "edit", "overrides": {...}, "note": "..."}

keyed by element id (see `ids` below). Anything not listed is "pending" and is treated as
rejected at publish time unless the caller passes accept_pending=True (e.g. "accept everything
above a confidence threshold" is expressed by writing accept entries, not by a flag).

`apply(proposal, review)` returns a new proposal containing only accepted/edited elements with
overrides merged in. It never mutates the input.
"""
from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone

DECISIONS = ("accept", "reject", "edit")
SECTIONS = ("datasets", "fields", "relationships", "metrics", "glossary_terms")


# ------------------------------------------------------------------ element ids
def dataset_id(d: dict) -> str:
    return d["table"]


def field_id(d: dict, f: dict) -> str:
    return f"{d['table']}.{f['column']}"


def relationship_id(r: dict) -> str:
    return f"{r['from']}.{r['from_column']}->{r['to']}.{r['to_column']}"


def metric_id(m: dict) -> str:
    return f"{m['dataset']}::{m['name']}"


def term_id(t: dict) -> str:
    return t["name"]


# ------------------------------------------------------------------ io
def empty(run_id: str) -> dict:
    return {"run_id": run_id, "reviewed_at": None, "reviewed_by": None,
            **{s: {} for s in SECTIONS}}


def load(path: str, run_id: str) -> dict:
    if not os.path.exists(path):
        return empty(run_id)
    with open(path) as f:
        r = json.load(f)
    for s in SECTIONS:
        r.setdefault(s, {})
    return r


def save(path: str, review: dict, reviewed_by: str | None = None) -> None:
    review["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    if reviewed_by:
        review["reviewed_by"] = reviewed_by
    with open(path, "w") as f:
        json.dump(review, f, indent=2)


def decide(review: dict, section: str, element_id: str, decision: str,
           overrides: dict | None = None, note: str = "") -> None:
    if section not in SECTIONS or decision not in DECISIONS:
        raise ValueError(f"bad section/decision {section}/{decision}")
    entry = {"decision": decision}
    if overrides:
        entry["overrides"] = overrides
    if note:
        entry["note"] = note
    review[section][element_id] = entry


def bulk_accept(review: dict, proposal: dict, min_confidence: float) -> int:
    """Accept every pending element whose confidence >= min_confidence. Returns count."""
    n = 0

    def maybe(section, eid, conf):
        nonlocal n
        if eid not in review[section] and conf >= min_confidence:
            review[section][eid] = {"decision": "accept"}
            n += 1

    for d in proposal["datasets"]:
        maybe("datasets", dataset_id(d), d.get("confidence", 0))
        for f in d["fields"]:
            maybe("fields", field_id(d, f), f.get("confidence", 0))
    for r in proposal["relationships"]:
        if r.get("accepted"):  # an llm_rejected row stays rejected unless explicitly overturned
            maybe("relationships", relationship_id(r), r.get("confidence", 0))
    for m in proposal["metrics"]:
        maybe("metrics", metric_id(m), m.get("confidence", 0))
    for t in proposal["glossary_terms"]:
        maybe("glossary_terms", term_id(t), t.get("confidence", 0))
    return n


def cascade_dataset(review: dict, proposal: dict, table: str, decision: str) -> int:
    """Apply one decision to a dataset and every one of its fields (later per-field decisions override this)."""
    d = next((x for x in proposal["datasets"] if x["table"] == table), None)
    if d is None:
        raise KeyError(table)
    decide(review, "datasets", dataset_id(d), decision)
    for f in d["fields"]:
        decide(review, "fields", field_id(d, f), decision)
    return 1 + len(d["fields"])


def clear(review: dict, section: str | None = None) -> None:
    for s in ([section] if section else SECTIONS):
        review[s] = {}


def summary(review: dict, proposal: dict) -> dict:
    """Decision counts per section, including pending, for the review page header."""
    totals = {
        "datasets": [dataset_id(d) for d in proposal["datasets"]],
        "fields": [field_id(d, f) for d in proposal["datasets"] for f in d["fields"]],
        "relationships": [relationship_id(r) for r in proposal["relationships"]],
        "metrics": [metric_id(m) for m in proposal["metrics"]],
        "glossary_terms": [term_id(t) for t in proposal["glossary_terms"]],
    }
    out = {}
    for s, ids in totals.items():
        c = {"accept": 0, "reject": 0, "edit": 0, "pending": 0, "total": len(ids)}
        for i in ids:
            c[review.get(s, {}).get(i, {}).get("decision", "pending")] += 1
        out[s] = c
    return out


def decisions_by_id(review: dict) -> dict:
    """{section: {id: decision}} for templates."""
    return {s: {k: v.get("decision", "pending") for k, v in review.get(s, {}).items()} for s in SECTIONS}


# ------------------------------------------------------------------ apply
def _status(review: dict, section: str, eid: str) -> tuple[str, dict]:
    e = review.get(section, {}).get(eid)
    if not e:
        return "pending", {}
    return e.get("decision", "pending"), e.get("overrides", {}) or {}


def apply(proposal: dict, review: dict, accept_pending: bool = False) -> dict:
    """Return a proposal containing only accepted elements, with overrides merged in."""
    keep = {"accept", "edit"} | ({"pending"} if accept_pending else set())
    out = copy.deepcopy(proposal)

    datasets = []
    for d in out["datasets"]:
        st, ov = _status(review, "datasets", dataset_id(d))
        if st not in keep:
            continue
        fields = []
        for f in d["fields"]:
            fst, fov = _status(review, "fields", field_id(d, f))
            if fst not in keep:
                continue
            f.update(fov)
            fields.append(f)
        d.update({k: v for k, v in ov.items() if k != "fields"})
        d["fields"] = fields
        datasets.append(d)
    out["datasets"] = datasets
    kept_tables = {d["table"] for d in datasets}
    all_tables = {d["table"] for d in proposal["datasets"]}

    rels = []
    for r in out["relationships"]:
        st, ov = _status(review, "relationships", relationship_id(r))
        if st == "pending" and not r.get("accepted"):
            continue  # llm_rejected stays out unless the reviewer overturns it
        if st not in keep:
            continue
        r.update(ov)
        r["accepted"] = True
        # drop only when an end is a table the reviewer rejected; anything else (a typo in an edit,
        # a wrong column) is left in for the validator to report
        if all(t in kept_tables or t not in all_tables for t in (r["from"], r["to"])):
            rels.append(r)
    out["relationships"] = rels

    metrics = []
    for m in out["metrics"]:
        st, ov = _status(review, "metrics", metric_id(m))
        if st not in keep:
            continue
        m.update(ov)
        if m["dataset"] in kept_tables:
            metrics.append(m)
    out["metrics"] = metrics

    terms = []
    for t in out["glossary_terms"]:
        st, ov = _status(review, "glossary_terms", term_id(t))
        if st not in keep:
            continue
        t.update(ov)
        terms.append(t)
    out["glossary_terms"] = terms

    out["review"] = {"run_id": review.get("run_id"), "reviewed_at": review.get("reviewed_at"),
                     "reviewed_by": review.get("reviewed_by")}
    return out
