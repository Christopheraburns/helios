"""
Job: helios-crawl
Crawls one Helios-DS dataset into helios_index (python -m apps.helios.crawler crawl).

``crawl_jobs.launch`` starts runs of this Job, passing what to crawl in the run's
environment:

  HELIOS_CRAWL_DATASET       a READY Helios-DS dataset ID
  HELIOS_CRAWL_FULL          "1" to re-fetch and re-analyze everything
  HELIOS_CRAWL_REQUESTED_BY  who asked for the crawl (recorded on the run)
  HELIOS_CRAWL_NOTE          the requester's note (recorded on the run)
  HELIOS_CRAWL_STRATEGY      "deterministic" (default) or "llm" (the LLM crawler)

The crawl connects as this Job's WORKLOAD_USER. To crawl as the crawler machine
user, set WORKLOAD_USER and WORKLOAD_PASSWORD in the Job's environment.
"""
import os
import sys
from pathlib import Path

ROOT = os.environ.get("HELIOS_ROOT") or str(Path(__file__).resolve().parents[3])
for path in (
    ROOT,
    os.path.join(ROOT, "packages", "helios-cortex", "src"),
    os.path.join(ROOT, "packages", "helios-cloudera", "src"),
):
    if path not in sys.path:
        sys.path.insert(0, path)

from apps.helios.crawler.__main__ import main  # noqa: E402

dataset = os.environ.get("HELIOS_CRAWL_DATASET", "").strip()
if not dataset:
    raise SystemExit("Nothing to crawl: start crawls with crawl_jobs.launch, which sets HELIOS_CRAWL_DATASET.")
argv = ["crawl", "--dataset", dataset]
if os.environ.get("HELIOS_CRAWL_FULL") == "1":
    argv.append("--full")
if os.environ.get("HELIOS_CRAWL_STRATEGY", "").strip():
    argv += ["--strategy", os.environ["HELIOS_CRAWL_STRATEGY"].strip()]
print(f"helios-crawl: {' '.join(argv)} (requested by {os.environ.get('HELIOS_CRAWL_REQUESTED_BY') or 'unknown'})", flush=True)
status = main(argv)
if status:
    raise SystemExit(status)
