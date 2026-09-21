"""
Job: propose
Reads runs/<run_id>/harvest.json and profile.json, asks the configured LLM to describe tables and
columns, propose glossary terms, decide suggested relationships and propose metrics, and writes
runs/<run_id>/propose.json.

Environment:
  HELIOS_RUN_ID       run to propose for (default: latest)
  LLM_PROVIDER        anthropic | openai   (default: anthropic if ANTHROPIC_API_KEY is set)
  ANTHROPIC_API_KEY, ANTHROPIC_MODEL          for the Anthropic API
  INFERENCE_BASE_URL, INFERENCE_API_KEY, INFERENCE_MODEL   for Cloudera AI Inference (OpenAI-compatible)
  HELIOS_TABLES       optional comma-separated subset of database.table to propose for (useful for testing)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.environ.get("HELIOS_ROOT") or os.path.join(os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios"), "jobs"))
from _common import latest_run_id, read_json, run_path, write_json  # noqa: E402

from helios_core.llm import llm_from_env  # noqa: E402
from helios_core.propose import Proposer  # noqa: E402

llm = llm_from_env()
if llm is None:
    raise SystemExit("no LLM configured: set ANTHROPIC_API_KEY (and optionally ANTHROPIC_MODEL), "
                     "or INFERENCE_BASE_URL / INFERENCE_MODEL for Cloudera AI Inference")

run_id = os.environ.get("HELIOS_RUN_ID") or latest_run_id()
if not run_id:
    raise SystemExit("no run found; run jobs/harvest.py and jobs/profile.py first")
harvest = read_json(run_path(run_id, "harvest.json"))
profile = read_json(run_path(run_id, "profile.json"))

subset = [t.strip() for t in os.environ.get("HELIOS_TABLES", "").split(",") if t.strip()]
if subset:
    harvest["tables"] = [t for t in harvest["tables"] if f"{t['database']}.{t['table']}" in subset]

print(f"propose run {run_id}: {len(harvest['tables'])} tables via {llm.provider}/{llm.model}")
result = Proposer(llm).run(harvest, profile)
print(f"datasets={len(result['datasets'])} relationships={len(result['relationships'])} "
      f"metrics={len(result['metrics'])} proposed_terms={len(result['glossary_terms'])} llm_calls={result['llm']['calls']}")
write_json(run_path(run_id, "propose.json"), result)
