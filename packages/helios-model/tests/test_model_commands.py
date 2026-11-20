"""helios harvest / profile / propose / publish (burndown 5.5): the facades from the
command line, as a laptop user or a Workbench Job runs them."""
import io

import duckdb
import pytest

from helios_cortex import plugins
from helios_cortex.cli import main
from helios_cortex.registry import SqlRegistry
from helios_cortex.runs import RunStore
from helios_cortex.settings import Settings
from helios_model import discovery


def helios(*argv):
    out, err = io.StringIO(), io.StringIO()
    return main(list(argv), out=out, stderr=err), out.getvalue(), err.getvalue()


class LLM:
    provider, model, calls = "scripted", "m", 0

    def complete_json(self, system, user):
        if "fact_tables" in user:
            return {"metrics": [{"name": "Revenue", "dataset": "shop.orders", "expression": "SUM(o_amount)",
                                 "confidence": 0.8, "rationale": "Money in."}]}
        if '"table": "shop.orders"' in user:
            return {"name": "Orders", "kind": "fact", "confidence": 0.9,
                    "fields": [{"column": "o_amount", "role": "measure", "confidence": 0.9}]}
        if '"table"' in user:
            return {"name": "Customers", "kind": "dimension", "confidence": 0.4, "fields": []}
        return {"decisions": []}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("HELIOS_CONFIG", "HELIOS_PROFILE", "HELIOS_CALLER_TOKEN", "HELIOS_REGISTRY_URL", "HELIOS_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)
    with duckdb.connect(str(tmp_path / "warehouse.duckdb")) as db:
        db.execute("CREATE SCHEMA shop")
        db.execute("CREATE TABLE shop.customers AS SELECT i AS c_customer_id, 'c' || i AS c_name FROM range(1, 21) t(i)")
        db.execute("CREATE TABLE shop.orders AS SELECT i AS o_order_id, 1 + i % 20 AS o_customer_id, "
                   "i * 1.5 AS o_amount FROM range(1, 101) t(i)")
    assert helios("init")[0] == 0
    config = tmp_path / "helios.toml"
    config.write_text(config.read_text().replace('# data_dir = "helios-data"', 'data_dir = "helios-data"'))
    monkeypatch.setattr(discovery.Proposer, "_client", lambda self: LLM())    # the configured LLM, scripted
    assert helios("source", "add", "warehouse", "--connection", "default")[0] == 0
    assert helios("model", "add", "sales", "--name", "Sales", "--source", "warehouse")[0] == 0
    return tmp_path


def test_helios_model_registers_its_commands():
    assert {"harvest", "profile", "propose", "proposal", "publish"} <= set(plugins.names(plugins.COMMANDS))


def test_harvest_to_publish_from_the_command_line(project):
    status, out, _ = helios("harvest", "--source", "warehouse", "--database", "shop", "--model", "sales",
                            "--run-id", "20261110T120000Z", "--as", "alice")
    assert status == 0
    assert out.splitlines() == ["20261110T120000Z", "harvested 2 tables for model sales"]

    status, out, err = helios("profile")                       # the latest run
    assert status == 0 and out.splitlines()[0] == "20261110T120000Z"
    assert "profiled 2 tables: 1 relationships accepted" in err        # progress on stderr

    status, out, _ = helios("propose", "--run", "20261110T120000Z")
    assert status == 0
    assert "proposed 2 datasets, 1 relationships, 1 metrics" in out

    status, _, err = helios("publish")                         # nothing accepted yet: nothing to publish
    assert status == 1 and err.startswith("helios: error: ")

    status, out, _ = helios("publish", "--min-confidence", "0.5", "--as", "bob")
    assert status == 0
    lines = out.splitlines()
    assert lines[:2] == ["20261110T120000Z", "accepted 8 pending elements"]   # all but customers (0.4)
    assert lines[2].startswith("published model sales revision ")

    settings = Settings.from_file(project / "helios.toml")
    [revision] = SqlRegistry.from_settings(settings).semantic_revisions("sales")
    assert (revision.published_by, revision.discovery_run_id) == ("bob", "20261110T120000Z")
    runs = RunStore.from_settings(settings)
    assert runs.load("20261110T120000Z", "harvest")["harvested_by"] == "alice"
    assert runs.load("20261110T120000Z", "review")["reviewed_by"] == "bob"


def test_mistakes_are_reported_in_one_line(project):
    status, _, err = helios("profile")
    assert (status, err) == (1, "helios: error: there are no runs yet; start one with `helios harvest`\n")
    status, _, err = helios("harvest", "--source", "lake", "--database", "shop", "--model", "sales")
    assert (status, err) == (1, "helios: error: no data source 'lake'; register it with `helios source add`\n")
    assert helios("harvest", "--source", "warehouse", "--model", "sales")[0] == 2      # --database is required
    assert helios("harvest", "--source", "warehouse", "--database", "shop", "--model", "sales",
                  "--run-id", "20261110T120000Z")[0] == 0
    status, _, err = helios("propose", "--table", "shop.returns")
    assert status == 1 and "did not harvest shop.returns" in err
    status, _, err = helios("publish", "--accept-pending")
    assert status == 1 and "has no propose; propose first" in err


def test_publishing_an_unregistered_model_is_refused(project):
    assert helios("harvest", "--source", "warehouse", "--database", "shop", "--model", "hr",
                  "--run-id", "20261110T120000Z")[0] == 0
    assert helios("profile")[0] == 0
    assert helios("propose")[0] == 0
    status, _, err = helios("publish", "--accept-pending")
    assert status == 1 and "model 'hr' is not in the registry" in err


def test_review_kept_in_a_file_from_the_command_line(project):
    """A host that keeps its model in version control: harvest named tables, show the
    proposal for review, publish the decisions recorded in a file."""
    import json

    run = "20261120T120000Z"
    status, out, _ = helios("harvest", "--source", "warehouse", "--database", "shop", "--model", "sales",
                            "--table", "shop.orders", "--table", "shop.customers", "--run-id", run)
    assert status == 0 and "harvested 2 tables" in out
    assert helios("profile")[0] == 0 and helios("propose")[0] == 0

    status, out, _ = helios("proposal", "show", "--out", "PROPOSAL.md")
    assert status == 0 and "PROPOSAL.md" in out
    md = (project / "PROPOSAL.md").read_text()
    assert f"run {run}" in md and "| `shop.orders` | Orders | 0.90 | pending |" in md
    assert "`SUM(o_amount)`" in md and "## Relationships (1: " in md
    status, out, _ = helios("proposal", "show", "--format", "json")
    elements = json.loads(out)["elements"]
    assert {e["section"] for e in elements} == {"datasets", "fields", "relationships", "metrics"}

    review = {"run_id": run, "datasets": {"shop.orders": {"decision": "accept"},
                                          "shop.customers": {"decision": "edit", "overrides": {"name": "Buyers"}}},
              "fields": {"shop.orders.o_amount": {"decision": "accept"}},
              "metrics": {"shop.orders::Revenue": {"decision": "accept", "note": "checked"}}}
    (project / "review.json").write_text(json.dumps(review))
    status, out, _ = helios("publish", "--review", "review.json", "--as", "carol")
    assert status == 0, out
    assert "decisions from review.json" in out and "published model sales revision " in out
    runs = RunStore.from_settings(Settings.from_file(project / "helios.toml"))
    kept = runs.load(run, "review")
    assert kept["reviewed_by"] == "carol" and kept["datasets"]["shop.customers"]["overrides"] == {"name": "Buyers"}

    # The published model, exported for shipping with the host.
    status, out, _ = helios("model", "export", "sales", "--out", "model.yaml")
    assert status == 0 and "model.yaml" in out
    exported = (project / "model.yaml").read_text()
    assert "Buyers" in exported and "Revenue" in exported
    status, out, _ = helios("model", "export", "sales", "--format", "json")
    assert json.loads(out)["name"] == "sales"
    status, _, err = helios("model", "export", "nothing")
    assert status == 1 and "publish it first" in err

    # A review for another run, or of an element the run doesn't have, is refused.
    (project / "other.json").write_text(json.dumps({"run_id": "20200101T000000Z", "datasets": {}}))
    status, _, err = helios("publish", "--review", "other.json")
    assert status == 1 and "is for run '20200101T000000Z'" in err
    (project / "bad.json").write_text(json.dumps({"datasets": {"shop.returns": {"decision": "accept"}}}))
    status, _, err = helios("publish", "--review", "bad.json")
    assert status == 1 and "shop.returns" in err and "doesn't have" in err
