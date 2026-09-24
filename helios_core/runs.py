"""Read access to discovery runs on the project filesystem (runs/<run_id>/*.json)."""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Any

ROOT = os.environ.get("HELIOS_ROOT") or os.path.join(os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios")
RUNS_DIR = os.environ.get("HELIOS_RUNS_DIR") or os.path.join(ROOT, "runs")

STAGES = ("harvest", "profile", "propose")
LIFECYCLE_STATUSES = (
    "queued",
    "running",
    "completed",
    "completed_with_warnings",
    "failed",
    "cancelled",
)

_STAGE_TIMESTAMPS = {
    "harvest": "harvested_at",
    "profile": "profiled_at",
    "propose": "proposed_at",
}


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _valid_run_id(run_id: str) -> bool:
    return bool(run_id) and "/" not in run_id and "\\" not in run_id and ".." not in run_id


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _proposal_counts(proposal: dict | None) -> dict[str, int]:
    proposal = proposal or {}
    datasets = [
        dataset
        for dataset in _as_list(proposal.get("datasets"))
        if isinstance(dataset, dict)
    ]
    return {
        "datasets": len(datasets),
        "fields": sum(
            len(_as_list(dataset.get("fields")))
            for dataset in datasets
        ),
        "relationships": len(_as_list(proposal.get("relationships"))),
        "metrics": len(_as_list(proposal.get("metrics"))),
        "glossary_terms": len(_as_list(proposal.get("glossary_terms"))),
    }


def _artifact_model_id(artifacts: dict[str, dict | None]) -> str | None:
    return next(
        (
            artifact.get("model_id")
            for artifact in artifacts.values()
            if isinstance(artifact, dict) and artifact.get("model_id")
        ),
        None,
    )


def list_runs() -> list[dict]:
    if not os.path.isdir(RUNS_DIR):
        return []
    out = []
    for rid in sorted(os.listdir(RUNS_DIR), reverse=True):
        d = os.path.join(RUNS_DIR, rid)
        if not os.path.isdir(d):
            continue
        item = detail(rid)
        item["updated"] = datetime.fromtimestamp(
            os.path.getmtime(d), tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")
        out.append(item)
    return out


def load(run_id: str, stage: str) -> dict | None:
    if stage not in STAGES or not _valid_run_id(run_id):
        return None
    p = os.path.join(RUNS_DIR, run_id, f"{stage}.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            value = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


_RELATIONSHIP_FIELDS = (
    "from",
    "from_column",
    "to",
    "to_column",
    "name_score",
    "to_rows",
    "distinct_values",
    "unmatched",
    "match_ratio",
)


def _relationship_evidence(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    projected = {}
    for field in _RELATIONSHIP_FIELDS:
        item = value.get(field)
        if field in {"from", "from_column", "to", "to_column"}:
            if isinstance(item, str) and item:
                projected[field] = item
        elif _finite_number(item):
            projected[field] = item
    return projected if projected.get("from") and projected.get("to") else None


def _relationships(profile: dict, key: str) -> list[dict]:
    return [
        projected
        for value in _as_list(profile.get(key))
        if (projected := _relationship_evidence(value)) is not None
    ]


def _table_identity(table: dict) -> str | None:
    database = table.get("database")
    name = table.get("table")
    if not isinstance(database, str) or not isinstance(name, str):
        return None
    return f"{database}.{name}"


def _primary_keys(table: dict) -> list[dict]:
    projected = []
    for candidate in _as_list(table.get("primary_keys")):
        if not isinstance(candidate, dict) or not isinstance(
            candidate.get("column"), str
        ):
            continue
        item = {"column": candidate["column"]}
        if _finite_number(candidate.get("confidence")):
            item["confidence"] = candidate["confidence"]
        projected.append(item)
    return projected


def _glossary_matches(harvest: dict) -> dict[str, list[str]]:
    matches: dict[str, list[str]] = {}
    for term in _as_list(harvest.get("glossary_terms")):
        if not isinstance(term, dict) or not isinstance(term.get("name"), str):
            continue
        for column in _as_list(term.get("columns")):
            if not isinstance(column, str):
                continue
            identity = column.split("@", 1)[0]
            if identity:
                matches.setdefault(identity, []).append(term["name"])
    return matches


def _safe_column_profile(column: dict) -> dict:
    result = {}
    if isinstance(column.get("type"), str):
        result["type"] = column["type"]
    for key in ("null_rate", "ndv", "ndv_exact"):
        if _finite_number(column.get(key)):
            result[key] = column[key]
    for key in ("min", "max"):
        if isinstance(column.get(key), (str, int, float, bool)) and (
            not isinstance(column.get(key), float)
            or math.isfinite(column[key])
        ):
            result[key] = column[key]
    return result


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def profile_summary(run_id: str) -> dict | None:
    """Return safe physical profile evidence from one exact historical run."""
    harvest = load(run_id, "harvest")
    profile = load(run_id, "profile")
    if harvest is None and profile is None:
        return None
    harvest = harvest or {}
    profile = profile or {}
    harvested = {
        identity: table
        for table in _as_list(harvest.get("tables"))
        if isinstance(table, dict)
        if (identity := _table_identity(table)) is not None
    }
    profiled = _as_dict(profile.get("tables"))
    tables = []
    for identity in sorted(set(harvested) | set(profiled)):
        harvested_table = harvested.get(identity, {})
        profiled_table = (
            profiled.get(identity)
            if isinstance(profiled.get(identity), dict)
            else {}
        )
        stats = _as_dict(profiled_table.get("stats"))
        columns = _as_dict(stats.get("columns"))
        harvested_columns = _as_list(harvested_table.get("columns"))
        tables.append(
            {
                "table_id": identity,
                "harvested": identity in harvested,
                "profiled": identity in profiled and bool(profiled_table),
                "column_count": (
                    len(columns) if columns else len(harvested_columns)
                ),
                "row_count": next(
                    (
                        value
                        for value in (
                            stats.get("row_count"),
                            harvested_table.get("row_count"),
                        )
                        if _finite_number(value)
                    ),
                    None,
                ),
                "primary_key_candidates": _primary_keys(profiled_table),
            }
        )
    return {
        "run_id": run_id,
        "profiled_at": (
            profile.get("profiled_at")
            if isinstance(profile.get("profiled_at"), str)
            else None
        ),
        "engine": next(
            (
                value
                for value in (profile.get("engine"), harvest.get("engine"))
                if isinstance(value, str)
            ),
            None,
        ),
        "tables": tables,
        "relationships": _relationships(profile, "relationships"),
        "suggested_relationships": _relationships(
            profile, "suggested_relationships"
        ),
        "rejected_candidates": _relationships(profile, "rejected_candidates"),
    }


def table_profile(run_id: str, table_identity: str) -> dict | None:
    """Return one historical physical table profile without selecting a newer run."""
    profile = load(run_id, "profile")
    if profile is None:
        return None
    table = _as_dict(profile.get("tables")).get(table_identity)
    if not isinstance(table, dict):
        return None
    stats = _as_dict(table.get("stats"))
    columns = _as_dict(stats.get("columns"))
    harvest = load(run_id, "harvest") or {}
    glossary = _glossary_matches(harvest)
    projected_columns = {
        name: {
            **_safe_column_profile(column),
            "glossary_terms": glossary.get(f"{table_identity}.{name}", []),
        }
        for name, column in columns.items()
        if isinstance(name, str) and isinstance(column, dict)
    }
    relationships = _relationships(profile, "relationships")
    return {
        "run_id": run_id,
        "table_id": table_identity,
        "profiled_at": (
            profile.get("profiled_at")
            if isinstance(profile.get("profiled_at"), str)
            else None
        ),
        "engine": (
            profile.get("engine")
            if isinstance(profile.get("engine"), str)
            else None
        ),
        "row_count": (
            stats.get("row_count")
            if _finite_number(stats.get("row_count"))
            else None
        ),
        "column_count": len(projected_columns),
        "columns": projected_columns,
        "primary_key_candidates": _primary_keys(table),
        "relationships": [
            relationship
            for relationship in relationships
            if table_identity
            in {
                relationship.get("from"),
                relationship.get("to"),
                relationship.get("from_table"),
                relationship.get("to_table"),
            }
        ],
    }


def detail(run_id: str) -> dict:
    """Project persisted artifacts into an evidence-backed run DTO."""
    artifacts = {stage: load(run_id, stage) for stage in STAGES}
    timestamps = {
        stage: (
            artifact.get(_STAGE_TIMESTAMPS[stage]) or None
            if artifact is not None
            else None
        )
        for stage, artifact in artifacts.items()
    }
    parsed = [
        timestamp
        for value in timestamps.values()
        if (timestamp := _parse_timestamp(value)) is not None
    ]
    started_at = min(parsed).isoformat() if parsed else None
    completed_at = max(parsed).isoformat() if parsed else None
    duration_seconds = (
        (max(parsed) - min(parsed)).total_seconds() if len(parsed) > 1 else None
    )
    harvest = artifacts["harvest"] or {}
    profile = artifacts["profile"] or {}
    proposal = artifacts["propose"] or {}
    harvest_tables = [
        table
        for table in _as_list(harvest.get("tables"))
        if isinstance(table, dict)
    ]
    profile_tables = _as_dict(profile.get("tables"))
    proposal_counts = _proposal_counts(artifacts["propose"])
    phases = []
    for stage in STAGES:
        artifact = artifacts[stage]
        counts: dict[str, int] = {}
        if stage == "harvest" and artifact is not None:
            tables = [
                table
                for table in _as_list(artifact.get("tables"))
                if isinstance(table, dict)
            ]
            counts = {
                "tables": len(tables),
                "columns": sum(
                    len(_as_list(table.get("columns")))
                    for table in tables
                ),
                "glossary_terms": len(_as_list(artifact.get("glossary_terms"))),
                "queries": len(
                    _as_list(_as_dict(artifact.get("queries")).get("statements"))
                ),
            }
        elif stage == "profile" and artifact is not None:
            counts = {
                "tables": len(_as_dict(artifact.get("tables"))),
                "relationships": len(_as_list(artifact.get("relationships"))),
                "suggested_relationships": len(
                    _as_list(artifact.get("suggested_relationships"))
                ),
                "rejected_candidates": len(
                    _as_list(artifact.get("rejected_candidates"))
                ),
            }
        elif stage == "propose" and artifact is not None:
            counts = proposal_counts
        phases.append(
            {
                "id": stage,
                "name": stage.title(),
                "status": "completed" if artifact is not None else None,
                "started_at": None,
                "completed_at": timestamps[stage],
                "duration_seconds": None,
                "counts": counts,
                "available": artifact is not None,
            }
        )

    # A complete proposal is evidence that this artifact-backed pipeline finished.
    status = "completed" if artifacts["propose"] is not None else None
    return {
        "id": run_id,
        "type": "discovery",
        "model_id": _artifact_model_id(artifacts),
        "data_source": {
            "engine": harvest.get("engine") or profile.get("engine") or None,
            "databases": _as_list(harvest.get("databases")),
        },
        "initiator": None,
        "started_at": started_at,
        "completed_at": completed_at if status else None,
        "duration_seconds": duration_seconds if status else None,
        "status": status,
        "progress": 100 if status else None,
        "warnings": [],
        "errors": [],
        "stages": {
            stage: artifacts[stage] is not None for stage in STAGES
        },
        "phases": phases,
        "counts": {
            "discovered": {
                "tables": len(harvest_tables),
                "columns": sum(
                    len(_as_list(table.get("columns")))
                    for table in harvest_tables
                ),
                "glossary_terms": len(_as_list(harvest.get("glossary_terms"))),
            },
            "profiled": {
                "tables": len(profile_tables),
                "relationships": len(_as_list(profile.get("relationships"))),
                "suggested_relationships": len(
                    _as_list(profile.get("suggested_relationships"))
                ),
                "rejected_candidates": len(
                    _as_list(profile.get("rejected_candidates"))
                ),
            },
            "proposed": proposal_counts,
        },
        "provenance": {
            "llm": proposal.get("llm") if isinstance(proposal.get("llm"), dict) else None
        },
    }


def summary(run_id: str) -> dict:
    h, p = load(run_id, "harvest"), load(run_id, "profile")
    s = {"id": run_id, "model_id": (h or {}).get("model_id") or (p or {}).get("model_id"),
         "harvest": None, "profile": None,
         "propose": os.path.exists(os.path.join(RUNS_DIR, run_id, "propose.json"))}
    if h:
        s["harvest"] = {"at": h.get("harvested_at", ""), "engine": h.get("engine", ""), "databases": h.get("databases", []),
                        "tables": len(h.get("tables", [])), "columns": sum(len(t["columns"]) for t in h.get("tables", [])),
                        "glossary_terms": len(h.get("glossary_terms", [])),
                        "queries": len(h.get("queries", {}).get("statements", [])),
                        "query_sources": h.get("queries", {}).get("sources", [])}
    if p:
        s["profile"] = {"at": p.get("profiled_at", ""), "tables": len(p.get("tables", {})),
                        "relationships": len(p.get("relationships", [])),
                        "suggested": len(p.get("suggested_relationships", [])),
                        "rejected": len(p.get("rejected_candidates", []))}
    return s
