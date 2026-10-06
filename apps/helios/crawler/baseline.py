"""The same-output gate for crawler changes (CG-0).

A crawl's result is reduced to a fingerprint: for each table the run wrote, the
number of rows and a hash of their IDs and content, plus the scorecard when
ground truth is readable. Two crawls with the same fingerprint found exactly
the same things. A recorded fingerprint is a *baseline*; ``compare`` says where
a later crawl differs from it.

What is left out, because it legitimately differs between two crawls of the
same documents: the run ID on every row, and whether an asset was read again
or carried forward.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from helios_core.index import IndexStore
from helios_core.index.records import CrawlRunRecord

FORMAT = 1
# table -> the columns that are not part of what the crawl found
TABLES: dict[str, tuple[str, ...]] = {
    "helios_index.assets": ("crawl_run_id", "status", "status_detail"),
    "helios_index.segments": ("crawl_run_id",),
    "helios_index.mentions": ("crawl_run_id",),
    "helios_index.entities": ("crawl_run_id",),
    "helios_index.entity_links": ("crawl_run_id",),
    "helios_index.relationships": ("crawl_run_id",),
    "helios_index.claims": ("crawl_run_id",),
    "helios_index.claim_evidence": ("crawl_run_id",),
}
# Counts that depend on what the previous run left behind or on the model's
# availability, not on what was found.
VOLATILE_COUNTS = frozenset(
    {
        "analyzed",
        "carried_forward",
        "embedded",
        "embedding_failed",
        "llm_calls",
        "llm_cached",
        "llm_ms",
        "llm_tokens_in",
        "llm_tokens_out",
        "llm_rate_limited",
        "llm_cost_microusd",
    }
)


def _sha(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def table_fingerprint(index: IndexStore, table: str, crawl_run_id: str) -> dict[str, Any]:
    ignored = TABLES[table]
    rows = sorted(
        json.dumps(
            {k: v for k, v in record.model_dump(mode="json").items() if k not in ignored},
            sort_keys=True,
            separators=(",", ":"),
        )
        for record in index.read(table, {"crawl_run_id": crawl_run_id})
    )
    return {"rows": len(rows), "sha256": _sha(rows)}


def scorecard_fingerprint(metrics: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    """The headline numbers, and a hash of every metric except the description
    of the run itself (its ID and counts)."""
    body = {k: v for k, v in metrics.items() if k != "run"}
    return {
        "summary": summary,
        "sha256": _sha([json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)]),
    }


def fingerprint(
    index: IndexStore,
    run: CrawlRunRecord,
    *,
    truth_cursor: Callable[[], Any] | None = None,
    dataset_id: str | None = None,
) -> dict[str, Any]:
    """Everything a crawl found, reduced to counts and hashes. With
    ``truth_cursor`` and ``dataset_id`` the scorecard is included; nothing is
    written to the index."""
    result: dict[str, Any] = {
        "format": FORMAT,
        "source": run.source,
        "strategy": str((run.settings or {}).get("strategy") or "deterministic"),
        "recorded_from": {
            "crawl_run_id": run.crawl_run_id,
            "crawler_version": run.crawler_version,
            "ontology_version": run.ontology_version,
            "settings_version": run.settings_version,
            "settings_hash": run.settings_hash,
            "started_at": run.started_at,
        },
        "counts": {k: v for k, v in sorted(run.counts.items()) if k not in VOLATILE_COUNTS},
        "tables": {
            table.split(".", 1)[1]: table_fingerprint(index, table, run.crawl_run_id)
            for table in TABLES
        },
    }
    if truth_cursor is not None and dataset_id:
        from . import evaluate as harness

        truth = harness.load_truth(truth_cursor, dataset_id)
        metrics = harness.score(truth, harness.load_index(index, run.crawl_run_id), run)
        result["dataset_id"] = dataset_id
        result["scorecard"] = scorecard_fingerprint(metrics, harness.summarize(metrics))
    return result


def compare(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Where ``current`` differs from ``baseline``, in words; [] if it found the same."""
    differences: list[str] = []
    for key in ("source", "strategy"):
        if baseline.get(key) != current.get(key):
            differences.append(f"{key}: baseline {baseline.get(key)!r}, now {current.get(key)!r}")
    for name, before in baseline.get("tables", {}).items():
        after = current.get("tables", {}).get(name)
        if after is None:
            differences.append(f"{name}: not in this crawl's fingerprint")
        elif before["rows"] != after["rows"]:
            differences.append(f"{name}: {before['rows']} rows in the baseline, {after['rows']} now")
        elif before["sha256"] != after["sha256"]:
            differences.append(f"{name}: same number of rows ({before['rows']}), different content")
    before_counts, after_counts = baseline.get("counts", {}), current.get("counts", {})
    for name in sorted(set(before_counts) | set(after_counts)):
        if before_counts.get(name) != after_counts.get(name):
            differences.append(
                f"count {name}: baseline {before_counts.get(name)}, now {after_counts.get(name)}"
            )
    if "scorecard" in baseline:
        if "scorecard" not in current:
            differences.append("scorecard: not computed for this crawl")
        else:
            before_summary = baseline["scorecard"]["summary"]
            after_summary = current["scorecard"]["summary"]
            for name in sorted(set(before_summary) | set(after_summary)):
                if before_summary.get(name) != after_summary.get(name):
                    differences.append(
                        f"score {name}: baseline {before_summary.get(name)}, now {after_summary.get(name)}"
                    )
            if not differences and baseline["scorecard"]["sha256"] != current["scorecard"]["sha256"]:
                differences.append("scorecard: headline numbers match, a detailed metric differs")
    return differences
