"""
Job: helios-crawl
Crawls one data source into helios_index (python -m apps.helios.crawler crawl).

Runs are started by the Helios API (POST /api/v1/crawler/runs; the Crawler page's
"Start crawl"), which passes what to crawl in the run's environment:

  HELIOS_CRAWL_SOURCE        a data source ID registered in Helios, or
  HELIOS_CRAWL_DATASET       a READY Helios-DS dataset ID
  HELIOS_CRAWL_FULL          "1" to re-fetch and re-analyze everything
  HELIOS_CRAWL_REQUESTED_BY  who asked for the crawl (recorded on the run)
  HELIOS_CRAWL_NOTE          the requester's note (recorded on the run)

The crawl connects as this Job's WORKLOAD_USER. To crawl as the crawler machine
user, set WORKLOAD_USER and WORKLOAD_PASSWORD in the Job's environment.
"""
import os
import sys

ROOT = os.environ.get("HELIOS_ROOT") or os.path.join(os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios")
for path in (ROOT, os.path.join(ROOT, "shared")):
    if path not in sys.path:
        sys.path.insert(0, path)

from apps.helios.crawler.__main__ import main  # noqa: E402

source = os.environ.get("HELIOS_CRAWL_SOURCE", "").strip()
dataset = os.environ.get("HELIOS_CRAWL_DATASET", "").strip()
if bool(source) == bool(dataset):
    raise SystemExit(
        "Nothing to crawl: start crawls from the Helios Crawler page or "
        "POST /api/v1/crawler/runs, which set HELIOS_CRAWL_SOURCE or HELIOS_CRAWL_DATASET."
    )
argv = ["crawl", "--source", source] if source else ["crawl", "--dataset", dataset]
if os.environ.get("HELIOS_CRAWL_FULL") == "1":
    argv.append("--full")
print(f"helios-crawl: {' '.join(argv)} (requested by {os.environ.get('HELIOS_CRAWL_REQUESTED_BY') or 'unknown'})", flush=True)
status = main(argv)
if status:
    raise SystemExit(status)
