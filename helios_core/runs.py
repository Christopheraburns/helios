"""Read access to discovery runs on the project filesystem (runs/<run_id>/*.json)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

ROOT = os.environ.get("HELIOS_ROOT") or os.path.join(os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios")
RUNS_DIR = os.environ.get("HELIOS_RUNS_DIR") or os.path.join(ROOT, "runs")

STAGES = ("harvest", "profile", "propose")


def list_runs() -> list[dict]:
    if not os.path.isdir(RUNS_DIR):
        return []
    out = []
    for rid in sorted(os.listdir(RUNS_DIR), reverse=True):
        d = os.path.join(RUNS_DIR, rid)
        if not os.path.isdir(d):
            continue
        stages = {s: os.path.exists(os.path.join(d, f"{s}.json")) for s in STAGES}
        out.append({"id": rid, "stages": stages,
                    "updated": datetime.fromtimestamp(os.path.getmtime(d), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")})
    return out


def load(run_id: str, stage: str) -> dict | None:
    if stage not in STAGES or "/" in run_id or ".." in run_id:
        return None
    p = os.path.join(RUNS_DIR, run_id, f"{stage}.json")
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def summary(run_id: str) -> dict:
    h, p = load(run_id, "harvest"), load(run_id, "profile")
    s = {"id": run_id, "harvest": None, "profile": None, "propose": os.path.exists(os.path.join(RUNS_DIR, run_id, "propose.json"))}
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
