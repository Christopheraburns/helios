"""The same-output gate on the sample documents (CG-0).

Crawls the seven sample documents against the sample warehouse and compares
what was found with the fingerprints recorded in ``baselines/``: every row of
every table, and every query sent to the warehouse. A change to the crawler
that alters any of it fails here, offline, in seconds.

A deliberate change of behaviour is recorded with:

    HELIOS_UPDATE_BASELINE=1 python -m pytest apps/helios/tests/test_crawler_baseline.py
"""

import hashlib
import json
import os
from pathlib import Path

import pytest
from apps.helios.crawler import baseline
from apps.helios.crawler.crawl import crawl
from helios_core.index.records import MentionRecord
from helios_core.index.store import duckdb_index_store
from test_crawler_llm import _connector, extractor
from test_crawler_resolution import CONFIG, gazetteer, warehouse  # noqa: F401 - fixtures
from crawler_samples import RETAIL_SETTINGS

BASELINES = Path(__file__).parent / "baselines"
# Not part of what a crawl found: which run it was recorded from.
COMPARED = ("source", "strategy", "counts", "tables", "warehouse_queries")


class Recorded:
    """A warehouse cursor factory that remembers every statement."""

    def __init__(self, factory):
        self.factory = factory
        self.statements: list[str] = []

    def __call__(self):
        outer = self
        cursor = self.factory()

        class Cursor:
            def execute(self, sql, params=None):
                outer.statements.append(json.dumps([sql, list(params or [])], default=str))
                return cursor.execute(sql, params) if params is not None else cursor.execute(sql)

            def __getattr__(self, name):
                return getattr(cursor, name)

        return Cursor()

    def fingerprint(self) -> dict:
        return {
            "count": len(self.statements),
            "sha256": hashlib.sha256("\n".join(sorted(self.statements)).encode()).hexdigest(),
        }


@pytest.fixture(autouse=True)
def stable_paths(tmp_path, monkeypatch):
    """The sample files' location is recorded on each asset. Reading them from a
    relative path keeps the fingerprint the same on every machine and run."""
    monkeypatch.chdir(tmp_path)


def run_crawl(tmp_path, warehouse, gazetteer, strategy, index=None):  # noqa: F811
    index = index or duckdb_index_store()
    index.ensure_tables()
    queries = Recorded(warehouse.cursor)
    run = crawl(
        index,
        _connector(Path(".")),
        "ds-1",
        actor="test",
        ontology_version="0.2.0",
        settings=None,
        settings_hash=RETAIL_SETTINGS.content_hash(),
        crawler_settings=RETAIL_SETTINGS,
        gazetteer=gazetteer,
        resolution=CONFIG,
        warehouse_cursor=queries,
        strategy=strategy,
        llm=extractor(tmp_path) if strategy == "llm" else None,
    )
    assert run.status == "SUCCEEDED"
    found = baseline.fingerprint(index, run)
    found["warehouse_queries"] = queries.fingerprint()
    return index, run, found


def comparable(found: dict) -> dict:
    return {key: found[key] for key in COMPARED}


@pytest.mark.parametrize("strategy", ["deterministic", "llm"])
def test_the_sample_crawl_matches_its_recorded_baseline(tmp_path, warehouse, gazetteer, strategy):  # noqa: F811
    path = BASELINES / f"sample-{strategy}.json"
    _, _, found = run_crawl(tmp_path, warehouse, gazetteer, strategy)
    if os.environ.get("HELIOS_UPDATE_BASELINE") == "1":
        path.write_text(json.dumps(comparable(found), indent=2, sort_keys=True) + "\n")
    recorded = json.loads(path.read_text())
    differences = baseline.compare(recorded, found)
    if recorded["warehouse_queries"] != found["warehouse_queries"]:
        differences.append(
            f"warehouse queries: {recorded['warehouse_queries']['count']} in the baseline, "
            f"{found['warehouse_queries']['count']} now, or different text"
        )
    assert not differences, (
        "The crawler found something different from its baseline:\n  "
        + "\n  ".join(differences)
        + "\nIf that is intended: HELIOS_UPDATE_BASELINE=1 python -m pytest "
        "apps/helios/tests/test_crawler_baseline.py"
    )


def test_the_baseline_is_reproduced_by_a_fresh_crawl_and_by_a_carried_forward_one(
    tmp_path, warehouse, gazetteer  # noqa: F811
):
    index, first_run, first = run_crawl(tmp_path, warehouse, gazetteer, "deterministic")
    _, _, fresh = run_crawl(tmp_path, warehouse, gazetteer, "deterministic")
    assert comparable(fresh) == comparable(first)

    _, carried_run, carried = run_crawl(tmp_path, warehouse, gazetteer, "deterministic", index)
    assert carried_run.counts["carried_forward"] == 7 and "analyzed" not in carried_run.counts
    assert carried_run.crawl_run_id != first_run.crawl_run_id
    assert baseline.compare(first, carried) == []
    assert carried["warehouse_queries"] == first["warehouse_queries"]


def test_the_rules_crawl_queries_the_warehouse_and_the_llm_crawl_does_not(
    tmp_path, warehouse, gazetteer  # noqa: F811
):
    _, _, rules = run_crawl(tmp_path, warehouse, gazetteer, "deterministic")
    _, _, llm = run_crawl(tmp_path, warehouse, gazetteer, "llm")
    assert rules["warehouse_queries"]["count"] > 0
    assert llm["warehouse_queries"]["count"] == 0


def test_a_single_changed_row_is_reported(tmp_path, warehouse, gazetteer):  # noqa: F811
    index, run, before = run_crawl(tmp_path, warehouse, gazetteer, "deterministic")
    mention = index.read("helios_index.mentions", {"crawl_run_id": run.crawl_run_id})[0]
    assert isinstance(mention, MentionRecord)

    index._execute(
        "UPDATE helios_index.mentions SET proposed_class = 'Other' WHERE mention_id = ?",
        [mention.mention_id],
    )
    assert baseline.compare(before, baseline.fingerprint(index, run)) == [
        f"mentions: same number of rows ({before['tables']['mentions']['rows']}), different content"
    ]

    index._execute("DELETE FROM helios_index.claims WHERE crawl_run_id = ?", [run.crawl_run_id])
    differences = baseline.compare(before, baseline.fingerprint(index, run))
    assert f"claims: {before['tables']['claims']['rows']} rows in the baseline, 0 now" in differences


def test_scores_are_compared_when_the_baseline_has_them():
    table = {"rows": 1, "sha256": "x"}
    recorded = {
        "source": "s",
        "strategy": "deterministic",
        "counts": {"claims": 2},
        "tables": {"claims": table},
        "scorecard": {"summary": {"claim_recall": 1.0, "mention_precision": 0.93}, "sha256": "a"},
    }
    same = json.loads(json.dumps(recorded))
    assert baseline.compare(recorded, same) == []

    worse = json.loads(json.dumps(recorded))
    worse["scorecard"] = {"summary": {"claim_recall": 0.98, "mention_precision": 0.93}, "sha256": "b"}
    worse["counts"]["claims"] = 1
    assert baseline.compare(recorded, worse) == [
        "count claims: baseline 2, now 1",
        "score claim_recall: baseline 1.0, now 0.98",
    ]

    detail = json.loads(json.dumps(recorded))
    detail["scorecard"]["sha256"] = "c"
    assert baseline.compare(recorded, detail) == [
        "scorecard: headline numbers match, a detailed metric differs"
    ]
    unscored = {k: v for k, v in recorded.items() if k != "scorecard"}
    assert baseline.compare(recorded, unscored) == ["scorecard: not computed for this crawl"]
