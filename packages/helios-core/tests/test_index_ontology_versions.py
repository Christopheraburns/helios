"""CR-0c: ontology versions recorded in helios_index; the disk cache is rebuilt from it."""

import itertools
from pathlib import Path

import pytest
import yaml
from helios_core.index import TABLES, ddl_statements
from helios_core.index import ontology_versions as versions
from helios_core.ontology.mapping import load_mappings
from helios_core.ontology.parser import parse

REPO_ROOT = Path(__file__).resolve().parents[3]
EXTENSION = REPO_ROOT / "ontology/customers/example-tenant/extension.yaml"
OSSIE = REPO_ROOT / "models/published/tpcds.ossie.yaml"


@pytest.fixture(scope="module")
def graph():
    mappings = load_mappings(REPO_ROOT / "ontology/mappings/ossie")
    model = yaml.safe_load(OSSIE.read_text())
    return parse(str(EXTENSION), version="0.2.0", mappings=mappings, ossie_model=model).graph


def clock():
    ticks = itertools.count()
    return lambda: f"2026-10-01T00:00:{next(ticks):02d}+00:00"



def test_impala_ddl_creates_iceberg_tables_in_helios_index():
    statements = ddl_statements()
    assert statements[0] == "CREATE DATABASE IF NOT EXISTS helios_index"
    for table in TABLES:
        create = next(s for s in statements if f"TABLE IF NOT EXISTS {table} " in s)
        assert "STORED AS ICEBERG" in create


def test_publish_is_idempotent_and_round_trips_the_graph(index, graph):
    record, created = versions.publish(index, graph, "ontology/x.yaml", "alice", clock())
    assert created and record.content_hash == graph.content_hash
    again, created_again = versions.publish(index, graph, "ontology/x.yaml", "bob", clock())
    assert not created_again and again.published_by == "alice"
    assert len(versions.versions(index)) == 1
    restored = versions.graph_of(versions.versions(index)[0])
    assert restored.content_hash == graph.content_hash
    assert restored.canonical_json() == graph.canonical_json()


def test_a_version_cannot_be_republished_with_different_content(index, graph):
    versions.publish(index, graph, "ontology/x.yaml", "alice")
    other = parse(str(EXTENSION), version="0.2.0").graph  # no mappings: different content
    with pytest.raises(versions.VersionConflict, match="new version"):
        versions.publish(index, other, "ontology/x.yaml", "alice")


def test_the_latest_activation_is_active(index, graph):
    tick = clock()
    assert versions.active(index) is None
    with pytest.raises(versions.UnknownVersion):
        versions.activate(index, "9.9.9", "alice", tick)
    versions.publish(index, graph, "ontology/x.yaml", "alice", tick)
    first = versions.activate(index, "0.2.0", "alice", tick)
    second = versions.activate(index, "0.2.0", "bob", tick)
    assert versions.active(index) == second and first.content_hash == graph.content_hash


def test_missing_from_cache(index, graph):
    versions.publish(index, graph, "ontology/x.yaml", "alice")
    assert [r.version for r in versions.missing_from_cache(index, set())] == ["0.2.0"]
    assert versions.missing_from_cache(index, {("0.2.0", graph.content_hash)}) == []


SCHEMA = "ontology/customers/example-tenant/extension.yaml"
