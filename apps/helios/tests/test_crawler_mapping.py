"""The crawler resolves against the active stored source mapping, never the repository
file (CG-6). Moved here from the console's mappings API tests (burndown 8.1); it goes to
helios-crawl with the crawler (Phase 10)."""
import copy
from pathlib import Path

import pytest
from helios_cortex.index import mappings as stored
from helios_cortex.index.store import duckdb_index_store
from helios_cortex.ontology.mapping import SourceMapping, load_mapping, resolution_config

REPO_ROOT = Path(__file__).resolve().parents[3]
SHIPPED = load_mapping(REPO_ROOT / "ontology/mappings/ossie/tpcds.yaml")
ALICE = "cloudera-workbench:alice"


@pytest.fixture
def index():
    store = duckdb_index_store()
    store.ensure_tables()
    return store


def test_the_crawler_reads_the_active_stored_mapping_not_the_file(index, capsys, monkeypatch):
    from apps.helios.crawler import __main__ as crawler_main

    assert crawler_main.resolution_for("0.2.0", index) == (None, None)
    assert "no mapping is active" in capsys.readouterr().out

    stored.save(index, SHIPPED, ALICE)
    document = copy.deepcopy(SHIPPED.model_dump(mode="json", by_alias=True))
    document["resolution"]["thresholds"]["alias_min_score"] = 0.97
    stricter = SourceMapping.model_validate(document)
    stored.save(index, stricter, ALICE)
    stored.activate(index, 2, ALICE)
    monkeypatch.setattr(  # reading the repository file at crawl time would be a regression
        "helios_cortex.ontology.mapping.load_mapping", lambda path: pytest.fail("read the mapping file")
    )
    config, used = crawler_main.resolution_for("0.2.0", index)
    # What the run records about the mapping it resolved against.
    assert (used["model"], used["version"], used["database"]) == ("tpcds.ossie.yaml", 2, "tpcds")
    assert used["content_hash"] == stored.get(index, 2).content_hash
    assert config.thresholds.alias_min_score == 0.97
    assert config == resolution_config(stricter)
    assert "mapping: tpcds.ossie.yaml version 2" in capsys.readouterr().out
    assert not hasattr(crawler_main, "MAPPINGS")
