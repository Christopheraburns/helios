"""examples/viewer, served on the DuckDB TPC-DS sample (burndown 8.6): the page, the
published model as a graph, a query's path from the compiler, the ontology graph, and
nothing it accepts but GET. The page's JavaScript is not run here; its API is."""
import importlib.util
import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

VIEWER = Path(__file__).resolve().parents[2] / "examples/viewer"


def load_serve():
    spec = importlib.util.spec_from_file_location("helios_viewer_serve", VIEWER / "serve.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def viewer(settings):
    """The viewer on the sample, serving on a free port; yields a GET function."""
    serve = load_serve()
    server = serve.make_server(serve.Viewer(settings), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def request(path, method="GET"):
        try:
            with urllib.request.urlopen(urllib.request.Request(base + path, method=method), timeout=60) as response:
                body = response.read().decode()
                return response.status, response.headers["Content-Type"], body
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers["Content-Type"], exc.read().decode()

    yield request
    server.shutdown()
    server.server_close()


def test_the_page_is_static_with_one_pinned_checked_dependency(viewer):
    status, content_type, page = viewer("/")
    assert status == 200 and content_type.startswith("text/html")
    scripts = re.findall(r'<script src="([^"]+)"\s+integrity="(sha384-[^"]+)"', page)
    assert [src for src, _ in scripts] == ["https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.30.2/cytoscape.min.js"]
    for path in ("/api/models", "/api/ontology", "/compile?"):
        assert path in page


def test_the_published_model_is_a_graph_of_datasets_and_relationships(viewer):
    status, _, body = viewer("/api/models")
    assert status == 200 and [m["name"] for m in json.loads(body)] == ["tpcds"]
    graph = json.loads(viewer("/api/models/tpcds")[2])
    datasets = {d["name"]: d for d in graph["datasets"]}
    assert datasets["store_sales"]["kind"] == "fact"
    revenue = next(m for m in datasets["store_sales"]["metrics"] if m["name"] == "store_sales_revenue")
    assert revenue["synonyms"] == ["Store Sales Revenue"] and "ss_net_paid" in revenue["expression"]
    assert any(f["qualified"] == "tpcds.date_dim.d_year" for f in datasets["date_dim"]["fields"])
    assert {(r["from"], r["to"]) for r in graph["relationships"]} >= {("store_sales", "date_dim"), ("store_sales", "store")}
    assert viewer("/api/models/nowhere")[0] == 404


def test_a_querys_path_is_the_compilers_fact_joins_and_columns(viewer):
    status, _, body = viewer("/api/models/tpcds/compile?metric=Store+Sales+Revenue"
                             "&dimension=tpcds.date_dim.d_year&dimension=tpcds.store.s_state")
    compiled = json.loads(body)
    assert status == 200 and compiled["fact"] == "store_sales" and compiled["dialect"] == "duckdb"
    graph = json.loads(viewer("/api/models/tpcds")[2])
    joined = {(r["from"], r["to"]) for r in graph["relationships"] if r["name"] in compiled["joins"]}
    assert joined == {("store_sales", "date_dim"), ("store_sales", "store")}
    assert compiled["columns"] == ["d_year", "s_state", "store_sales_revenue"]
    assert "GROUP BY" in compiled["sql"]
    assert viewer("/api/models/tpcds/compile?metric=Profit")[0] == 400
    assert viewer("/api/models/tpcds/compile")[0] == 400


def test_the_ontology_graph(viewer):
    pytest.importorskip("linkml_runtime", reason="reading the LinkML ontology needs helios-cortex[ontology]")
    status, _, body = viewer("/api/ontology")
    graph = json.loads(body)
    assert status == 200 and graph["content_hash"]
    labels = {n["label"] for n in graph["nodes"]}
    assert {"Class", "OssieElement"} <= labels                    # the example ontology and its mappings
    assert any(e["type"] == "MAPS_TO" for e in graph["edges"])


def test_the_viewer_changes_nothing(viewer, settings):
    for method in ("POST", "PUT", "DELETE"):
        status, _, body = viewer("/api/models/tpcds", method)
        assert status == 405 and "read-only" in body
