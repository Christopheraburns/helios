"""CR-7: projecting a crawl run from helios_index into Memgraph.

`FakeGraph` is a Bolt stand-in that understands exactly the statement shapes
`apps.helios.graph.index` emits (MERGE on keyed nodes and edges, MATCH-only
edge ends, per-label counts, DETACH DELETE), so idempotence and run isolation
are asserted on real counts. The API endpoint is tested against a DuckDB
helios_index and a recording gateway client. The last test runs against a real
Memgraph when one is installed and is skipped otherwise.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from apps.helios.graph import index, memgraph_process
from apps.helios.tests.test_crawler_evaluate import ONT, build_run
from helios_core.index.store import duckdb_index_store

RUN = "crawl_test"


# --- a Bolt fake with MERGE semantics ----------------------------------------------------

_NODE_MERGE = re.compile(r"UNWIND \$rows AS row MERGE \(n:(\w+) \{crawl_run_id: \$run, key: row\.key\}\) SET n \+= row\.properties$")
_IN_RUN = re.compile(r"UNWIND \$rows AS row MATCH \(n:(\w+) \{crawl_run_id: \$run, key: row\.key\}\) MATCH \(r:CrawlRun \{key: \$run\}\) MERGE \(n\)-\[:IN_RUN\]->\(r\)$")
_EDGE_MERGE = re.compile(
    r"UNWIND \$rows AS row MATCH \(a:(\w+) \{crawl_run_id: \$run, key: row\.from_key\}\) "
    r"MATCH \(b:(\w+) \{(crawl_run_id: \$run|version: \$version), key: row\.to_key\}\) "
    r"MERGE \(a\)-\[e:(\w+) \{key: row\.key\}\]->\(b\)(?: SET e \+= row\.properties)? RETURN count\(e\) AS merged$"
)
_DROP = re.compile(r"MATCH \(n:(\w+) \{crawl_run_id: \$run\}\) DETACH DELETE n$")
_COUNT_NODES = re.compile(r"MATCH \(n:(\w+) \{crawl_run_id: \$run\}\) RETURN count\(n\) AS n$")
_COUNT_EDGES = re.compile(r"MATCH \(n:(\w+) \{crawl_run_id: \$run\}\)-\[e\]->\(\) RETURN type\(e\) AS type, count\(e\) AS n$")


class FakeGraph:
    """Nodes keyed by (label, scope, key) where scope is a run id or an ontology
    version; edges keyed by (type, from, to, key). Only the statements the
    projection emits are understood; anything else is an error."""

    driver_name = "fake"

    def __init__(self, classes: dict[str, set[str]] | None = None) -> None:
        self.nodes: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.edges: dict[tuple[str, tuple, tuple, str], dict[str, Any]] = {}
        self.recorded: list[tuple[str, dict[str, Any]]] = []
        for version, names in (classes or {}).items():
            for name in names:
                self.nodes[("Class", version, name)] = {"key": name, "version": version}

    def statements(self) -> str:
        return "\n".join(statement for statement, _ in self.recorded)

    def close(self) -> None:
        pass

    def _find(self, label: str, scope: str, key: str) -> tuple | None:
        node = (label, scope, key)
        return node if node in self.nodes else None

    def query(self, cypher: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        cypher = " ".join(cypher.split())
        p = dict(parameters or {})
        self.recorded.append((cypher, p))
        run = p.get("run")
        if cypher.startswith("CREATE INDEX"):
            return []
        if cypher.startswith("MERGE (r:CrawlRun {key: $run})"):
            self.nodes[("CrawlRun", run, run)] = {
                "key": run, "crawl_run_id": run, "ontology_version": p["ontology_version"],
                "complete": False, "loaded_at": p["now"], "counts_json": None,
            }
            return []
        if cypher.startswith("MATCH (r:CrawlRun {key: $run}) SET r.complete = true"):
            node = self.nodes.get(("CrawlRun", run, run))
            if node:
                node.update(complete=True, counts_json=p["counts_json"])
            return []
        if cypher.startswith("MATCH (r:CrawlRun {key: $run}) RETURN"):
            node = self.nodes.get(("CrawlRun", run, run))
            return [self._state(node)] if node else []
        if cypher.startswith("MATCH (r:CrawlRun) RETURN"):
            states = [self._state(n) for (label, _, _), n in self.nodes.items() if label == "CrawlRun"]
            return sorted(states, key=lambda s: -s["loaded_at"])
        if m := _NODE_MERGE.match(cypher):
            for row in p["rows"]:
                props = self.nodes.setdefault((m[1], run, row["key"]), {"key": row["key"], "crawl_run_id": run})
                props.update(row["properties"])
            return []
        if m := _IN_RUN.match(cypher):
            target = self._find("CrawlRun", run, run)
            for row in p["rows"]:
                source = self._find(m[1], run, row["key"])
                if source and target:
                    self.edges.setdefault(("IN_RUN", source, target, ""), {})
            return []
        if m := _EDGE_MERGE.match(cypher):
            to_scope = run if m[3].startswith("crawl_run_id") else p["version"]
            merged = 0
            for row in p["rows"]:
                a = self._find(m[1], run, row["from_key"])
                b = self._find(m[2], to_scope, row["to_key"])
                if a and b:
                    props = self.edges.setdefault((m[4], a, b, row["key"]), {"key": row["key"]})
                    props.update(row.get("properties") or {})
                    merged += 1
            return [{"merged": merged}]
        if m := _DROP.match(cypher):
            doomed = {n for n in self.nodes if n[0] == m[1] and n[1] == run}
            for n in doomed:
                del self.nodes[n]
            self.edges = {k: v for k, v in self.edges.items() if k[1] not in doomed and k[2] not in doomed}
            return []
        if m := _COUNT_NODES.match(cypher):
            return [{"n": sum(1 for n in self.nodes if n[0] == m[1] and n[1] == run)}]
        if m := _COUNT_EDGES.match(cypher):
            counts: dict[str, int] = {}
            for (edge_type, source, _, _) in self.edges:
                if source[0] == m[1] and source[1] == run:
                    counts[edge_type] = counts.get(edge_type, 0) + 1
            return [{"type": t, "n": n} for t, n in counts.items()]
        raise AssertionError(f"FakeGraph does not understand: {cypher}")

    @staticmethod
    def _state(node: dict[str, Any]) -> dict[str, Any]:
        return {k: node[k] for k in ("crawl_run_id", "ontology_version", "complete", "loaded_at", "counts_json")}


# --- fixtures ----------------------------------------------------------------------------


@pytest.fixture
def store():
    s = duckdb_index_store()
    s.ensure_tables()
    return s


@pytest.fixture
def run_id(store):
    return build_run(store)


def rows_of(store, run_id: str, table: str) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json") for r in store.read(f"helios_index.{table}", {"crawl_run_id": run_id})]


def project(graph: FakeGraph, store, run_id: str, ontology_version: str = ONT) -> dict[str, dict[str, int]]:
    """Load a whole run the way the API does, table by table."""
    index.begin_run(graph, run_id, ontology_version)
    results = {}
    for table in index.TABLES:
        results[table] = index.load_batch(graph, run_id, table, rows_of(store, run_id, table))
    index.finish_run(graph, run_id)
    return results


CLASSES = {ONT: {"Item", "Return", "Customer", "Sale", "Store", "Reason", "Document", "Message"}}


# --- whitelists --------------------------------------------------------------------------


def test_whitelists_cover_the_structural_edges_and_the_ontology_relationship_classes():
    assert index.INDEX_NODE_LABELS == {"CrawlRun", "Asset", "Segment", "Mention", "Entity", "Claim"}
    assert index.STRUCTURAL_EDGE_TYPES <= index.INDEX_EDGE_TYPES
    assert index.RELATIONSHIP_TYPES <= index.INDEX_EDGE_TYPES
    assert {"Mentions", "About", "ReturnOf", "Contains", "LocatedAt", "PartyTo", "HasReason",
            "ReferencesRecord", "EvidenceFor"} == index.RELATIONSHIP_TYPES


def test_an_unknown_relationship_type_is_refused_before_anything_is_written():
    graph = FakeGraph()
    index.begin_run(graph, RUN, ONT)
    rows = [
        {"relationship_id": "r1", "relationship_type": "About", "source_kind": "asset", "source_id": "a",
         "target_kind": "entity", "target_id": "e", "resolved_by": "joint", "confidence": 0.9},
        {"relationship_id": "r2", "relationship_type": "DROP DATABASE", "source_kind": "entity", "source_id": "e",
         "target_kind": "entity", "target_id": "f", "resolved_by": "joint", "confidence": 0.9},
    ]
    before = len(graph.recorded)
    with pytest.raises(index.IndexGraphError, match=r"\['DROP DATABASE'\]"):
        index.load_batch(graph, RUN, "relationships", rows)
    assert len(graph.recorded) == before
    assert "DROP DATABASE" not in graph.statements()


def test_an_unknown_link_type_and_end_kind_are_refused():
    graph = FakeGraph()
    with pytest.raises(index.IndexGraphError, match="link_type"):
        index.load_batch(graph, RUN, "entity_links", [
            {"link_id": "l", "mention_id": "m", "entity_id": "e", "link_type": "Maybe", "resolved_by": "x", "score": 0.1}
        ])
    with pytest.raises(index.IndexGraphError, match="kind"):
        index.load_batch(graph, RUN, "relationships", [
            {"relationship_id": "r", "relationship_type": "About", "source_kind": "claim", "source_id": "a",
             "target_kind": "entity", "target_id": "e", "resolved_by": "x", "confidence": 0.1}
        ])
    with pytest.raises(index.IndexGraphError, match="unknown index table"):
        index.load_batch(graph, RUN, "crawl_runs", [{"x": 1}])


def test_only_whitelisted_labels_and_types_are_interpolated():
    with pytest.raises(index.IndexGraphError):
        index._checked_label("Malicious")
    with pytest.raises(index.IndexGraphError):
        index._checked_type("OWNS")
    assert index._checked_label("Class") == "Class"  # ontology classes are matched, never created


# --- begin / load / finish ---------------------------------------------------------------


def test_a_run_projects_into_the_expected_nodes_and_edges(store, run_id):
    graph = FakeGraph(CLASSES)
    results = project(graph, store, run_id)

    state = index.run_state(graph, run_id)
    assert state["complete"] is True and state["ontology_version"] == ONT
    counts = state["counts"]
    assert counts["nodes"] == {
        "CrawlRun": 1, "Asset": 3, "Segment": 6, "Mention": 7, "Entity": 7, "Claim": 3,
    }
    assert counts["edges"] == {
        "IN_RUN": 26,  # every non-run node
        "HAS_SEGMENT": 6,
        "FOUND_IN": 7,
        "SAME_AS": 5,
        "POSSIBLY_SAME_AS": 1,
        "INSTANCE_OF": 7,
        "About": 3, "Mentions": 1, "PartyTo": 2, "Contains": 1, "LocatedAt": 1, "HasReason": 1,
        "SUBJECT": 3,
        "OBJECT": 3,
        "EVIDENCE_FOR": 2,
    }
    assert results["entities"] == {"merged": 7, "skipped": 0, "without_class": 0}
    assert all(r["skipped"] == 0 for r in results.values())

    # Properties: index ids are the keys, locators are JSON strings, text is bounded.
    segment = next(p for (label, _, _), p in graph.nodes.items() if label == "Segment")
    assert isinstance(segment["locator"], str) and segment["has_structure"] is False
    asset = graph.nodes[("Asset", run_id, "a-email")]
    assert asset["uri"] == "a-email" and asset["ontology_class"] == "Message"
    sames = [props for (t, _, _, _), props in graph.edges.items() if t == "SAME_AS"]
    assert all({"resolved_by", "score"} <= set(p) for p in sames)
    evidence = [props for (t, _, _, _), props in graph.edges.items() if t == "EVIDENCE_FOR"]
    assert all("excerpt" in p and "locator" in p for p in evidence)


def test_segment_text_is_truncated_and_structure_flagged():
    graph = FakeGraph()
    index.begin_run(graph, RUN, ONT)
    index.load_batch(graph, RUN, "assets", [{"asset_id": "a", "source_locator": {"uri": "s3://x/a"}}])
    index.load_batch(graph, RUN, "segments", [
        {"segment_id": "s", "asset_id": "a", "segment_type": "page", "ordinal": 0,
         "locator": {"page": 1}, "text": "x" * 5000, "structure": {"fields": [1]}},
    ])
    node = graph.nodes[("Segment", RUN, "s")]
    assert len(node["text"]) == index.TEXT_LIMIT and node["has_structure"] is True
    assert graph.nodes[("Asset", RUN, "a")]["uri"] == "s3://x/a"


def test_entities_without_a_class_node_are_counted_not_linked(store, run_id):
    graph = FakeGraph({ONT: {"Item"}})
    results = project(graph, store, run_id)
    assert results["entities"]["without_class"] == 6
    assert index.counts(graph, run_id)["edges"]["INSTANCE_OF"] == 1


def test_edges_whose_ends_are_missing_are_skipped_not_invented(store, run_id):
    """Links before entities: the API enforces order, the gateway reports it."""
    graph = FakeGraph(CLASSES)
    index.begin_run(graph, run_id, ONT)
    index.load_batch(graph, run_id, "assets", rows_of(store, run_id, "assets"))
    index.load_batch(graph, run_id, "segments", rows_of(store, run_id, "segments"))
    index.load_batch(graph, run_id, "mentions", rows_of(store, run_id, "mentions"))
    links = rows_of(store, run_id, "entity_links")
    early = index.load_batch(graph, run_id, "entity_links", links)
    assert early == {"merged": 0, "skipped": len(links)}
    assert "Entity" not in {n[0] for n in graph.nodes}
    index.load_batch(graph, run_id, "entities", rows_of(store, run_id, "entities"))
    late = index.load_batch(graph, run_id, "entity_links", links)
    assert late == {"merged": len(links), "skipped": 0}


def test_loading_the_same_rows_twice_leaves_the_counts_unchanged(store, run_id):
    graph = FakeGraph(CLASSES)
    project(graph, store, run_id)
    first = index.counts(graph, run_id)
    for table in index.TABLES:
        index.load_batch(graph, run_id, table, rows_of(store, run_id, table), ontology_version=ONT)
    assert index.counts(graph, run_id) == first
    index.finish_run(graph, run_id)
    assert index.run_state(graph, run_id)["counts"] == first


def test_drop_run_removes_only_that_run(store, run_id):
    graph = FakeGraph(CLASSES)
    other = build_run(store)
    project(graph, store, run_id)
    project(graph, store, other)
    before = index.counts(graph, other)
    assert index.counts(graph, run_id) == before  # identical runs, identical graphs

    index.drop_run(graph, run_id)
    assert index.run_state(graph, run_id) is None
    assert index.counts(graph, run_id) == {"nodes": dict.fromkeys(sorted(index.INDEX_NODE_LABELS), 0), "edges": {}}
    assert index.counts(graph, other) == before
    assert index.run_state(graph, other)["complete"] is True
    # The ontology's Class nodes are never ours to delete.
    assert ("Class", ONT, "Item") in graph.nodes


def test_drop_and_reload_reproduce_the_same_counts(store, run_id):
    """The CR-7 done-when, against the fake."""
    graph = FakeGraph(CLASSES)
    project(graph, store, run_id)
    first = index.counts(graph, run_id)
    index.drop_run(graph, run_id)
    project(graph, store, run_id)
    assert index.counts(graph, run_id) == first


def test_loads_are_chunked_at_a_thousand_rows():
    graph = FakeGraph()
    index.begin_run(graph, RUN, ONT)
    rows = [{"asset_id": f"a{i}", "source_locator": {"key": f"k{i}"}} for i in range(2_500)]
    index.load_batch(graph, RUN, "assets", rows)
    merges = [p for s, p in graph.recorded if _NODE_MERGE.match(s)]
    assert [len(p["rows"]) for p in merges] == [1000, 1000, 500]


def test_list_runs_reports_each_loaded_run(store, run_id):
    graph = FakeGraph(CLASSES)
    project(graph, store, run_id)
    index.begin_run(graph, "crawl_partial", ONT)
    listed = {r["crawl_run_id"]: r for r in index.list_runs(graph)}
    assert listed[run_id]["complete"] is True and listed[run_id]["counts"]["nodes"]["Asset"] == 3
    assert listed["crawl_partial"]["complete"] is False and listed["crawl_partial"]["counts"] is None


# --- the API endpoint --------------------------------------------------------------------


class RecordingGateway:
    """Stands in for GraphGatewayClient: records every call and answers like the gateway."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.closed = False

    def begin_run(self, run_id, ontology_version, force=False):
        self.calls.append(("begin", (run_id, ontology_version, force)))
        return {"status": "created"}

    def load_rows(self, run_id, table, rows):
        self.calls.append(("load", (table, len(rows))))
        return {"merged": len(rows), "skipped": 0, **({"without_class": 1} if table == "entities" else {})}

    def finish_run(self, run_id):
        self.calls.append(("finish", run_id))
        return {"status": "complete", "counts": {"nodes": {"Asset": 3}, "edges": {}}}

    def run_state(self, run_id):
        self.calls.append(("state", run_id))
        return None if run_id == "absent" else {"crawl_run_id": run_id, "complete": True}

    def close(self):
        self.closed = True


@pytest.fixture
def api(store, monkeypatch):
    from apps.helios.console import crawler, ontology
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setitem(ontology._index, "store", store)
    monkeypatch.setattr(ontology, "index_store", lambda: store)
    app = FastAPI()
    app.include_router(crawler.crawler_router)
    return TestClient(app)


ALICE = {"x-forwarded-user": "alice"}


def test_project_requires_a_principal(api, run_id):
    assert api.post(f"/api/v1/crawler/runs/{run_id}:project").status_code == 401


def test_project_is_503_when_the_gateway_is_not_configured(api, run_id, monkeypatch):
    monkeypatch.delenv("HELIOS_GRAPH_GATEWAY_URL", raising=False)
    monkeypatch.delenv("HELIOS_GRAPH_TOKEN", raising=False)
    response = api.post(f"/api/v1/crawler/runs/{run_id}:project", headers=ALICE)
    assert response.status_code == 503
    assert "HELIOS_GRAPH_GATEWAY_URL" in response.json()["detail"]
    assert api.get(f"/api/v1/crawler/runs/{run_id}/projection").status_code == 503


def test_project_is_404_for_an_unknown_run(api, monkeypatch):
    from apps.helios.console import crawler

    monkeypatch.setattr(crawler, "_gateway", RecordingGateway)
    assert api.post("/api/v1/crawler/runs/nope:project", headers=ALICE).status_code == 404


def test_project_pushes_every_table_in_reference_order_in_batches(api, store, run_id, monkeypatch):
    from apps.helios.console import crawler

    gateway = RecordingGateway()
    monkeypatch.setattr(crawler, "_gateway", lambda: gateway)
    monkeypatch.setattr(crawler, "PROJECTION_BATCH", 4)
    response = api.post(f"/api/v1/crawler/runs/{run_id}:project", headers=ALICE, json={"force": True})
    assert response.status_code == 200, response.text
    body = response.json()

    assert gateway.calls[0] == ("begin", (run_id, ONT, True))
    assert gateway.calls[-1] == ("finish", run_id)
    loads = [c for kind, c in gateway.calls if kind == "load"]
    assert list(dict.fromkeys(t for t, _ in loads)) == list(crawler.PROJECTION_TABLES)
    # Mentions (7 rows) arrive as 4 + 3; nothing exceeds the batch size.
    assert [n for t, n in loads if t == "mentions"] == [4, 3]
    assert all(n <= 4 for _, n in loads)
    assert gateway.closed

    assert body["index_rows"] == {
        "assets": 3, "segments": 6, "mentions": 7, "entities": 7, "entity_links": 6,
        "relationships": 9, "claims": 3, "claim_evidence": 2,
    }
    assert body["tables"]["mentions"] == {"merged": 7, "skipped": 0}
    assert body["tables"]["entities"] == {"merged": 7, "skipped": 0, "without_class": 2}
    assert body["graph"] == {"nodes": {"Asset": 3}, "edges": {}}
    assert body["ontology_version"] == ONT


def test_project_passes_gateway_rejections_through(api, run_id, monkeypatch):
    from apps.helios.console import crawler
    from apps.helios.console.graph_client import GraphGatewayError

    class Rejecting(RecordingGateway):
        def load_rows(self, run_id, table, rows):
            raise GraphGatewayError(422, "unknown relationship_type(s) ['Owns']")

    monkeypatch.setattr(crawler, "_gateway", Rejecting)
    response = api.post(f"/api/v1/crawler/runs/{run_id}:project", headers=ALICE)
    assert response.status_code == 422
    assert "Owns" in response.json()["detail"]


def test_projection_view_proxies_the_gateway_state(api, run_id, monkeypatch):
    from apps.helios.console import crawler

    monkeypatch.setattr(crawler, "_gateway", RecordingGateway)
    assert api.get(f"/api/v1/crawler/runs/{run_id}/projection").json()["complete"] is True
    assert api.get("/api/v1/crawler/runs/absent/projection").status_code == 404


# --- the gateway client ------------------------------------------------------------------


def test_gateway_client_talks_to_the_gateway_app(monkeypatch):
    """Drive the real FastAPI gateway (with the fake graph) through the HTTP client."""
    from types import SimpleNamespace

    import httpx
    from apps.helios.console.graph_client import GraphGatewayClient, GraphGatewayError
    from apps.helios.graph import gateway
    from fastapi.testclient import TestClient

    graph = FakeGraph({ONT: {"Item"}})
    monkeypatch.setattr(gateway, "_TOKEN", "t")
    monkeypatch.setattr(gateway, "_memgraph", SimpleNamespace(running=True, pid=1, command=[], diagnostic_output=lambda: ""))
    monkeypatch.setitem(gateway._state, "client", graph)
    # The client is synchronous, so bridge it to the app through the TestClient.
    http = TestClient(gateway.app)

    def forward(request: httpx.Request) -> httpx.Response:
        reply = http.request(
            request.method, request.url.path, content=request.content,
            headers={k: v for k, v in request.headers.items() if k in ("authorization", "content-type")},
        )
        return httpx.Response(reply.status_code, content=reply.content, headers=dict(reply.headers))

    client = GraphGatewayClient("http://graph", "t", transport=httpx.MockTransport(forward))

    assert client.run_state(RUN) is None
    assert client.begin_run(RUN, ONT)["status"] == "created"
    assert client.load_rows(RUN, "entities", [{"entity_id": "e", "ontology_class": "Item", "canonical_name": "x"}]) == {
        "crawl_run_id": RUN, "table": "entities", "rows": 1, "merged": 1, "skipped": 0, "without_class": 0,
    }
    with pytest.raises(GraphGatewayError) as info:
        client.load_rows(RUN, "relationships", [{"relationship_id": "r", "relationship_type": "Owns",
                                                 "source_kind": "entity", "source_id": "e",
                                                 "target_kind": "entity", "target_id": "e"}])
    assert info.value.status == 422 and "Owns" in str(info.value.detail)
    finished = client.finish_run(RUN)
    assert finished["counts"]["nodes"]["Entity"] == 1 and finished["counts"]["edges"]["INSTANCE_OF"] == 1
    assert [r["crawl_run_id"] for r in client.list_runs()] == [RUN]
    assert client.run_state(RUN)["complete"] is True
    assert client.drop_run(RUN)["status"] == "dropped"
    assert client.run_state(RUN) is None


def test_gateway_client_requires_configuration(monkeypatch):
    from apps.helios.console.graph_client import GraphGatewayClient, GraphGatewayUnavailable

    monkeypatch.delenv("HELIOS_GRAPH_GATEWAY_URL", raising=False)
    monkeypatch.delenv("HELIOS_GRAPH_TOKEN", raising=False)
    with pytest.raises(GraphGatewayUnavailable):
        GraphGatewayClient()
    monkeypatch.setenv("HELIOS_GRAPH_GATEWAY_URL", "http://graph")
    monkeypatch.setenv("HELIOS_GRAPH_TOKEN", "t")
    GraphGatewayClient().close()


def test_gateway_client_reports_an_unreachable_gateway():
    import httpx
    from apps.helios.console.graph_client import GraphGatewayClient, GraphGatewayUnavailable

    def down(request):
        raise httpx.ConnectError("refused")

    client = GraphGatewayClient("http://graph", "t", transport=httpx.MockTransport(down))
    with pytest.raises(GraphGatewayUnavailable, match="unreachable"):
        client.run_state(RUN)


# --- against a real Memgraph, when one is installed -------------------------------------


def _memgraph_available() -> bool:
    try:
        memgraph_process.binary_path()
    except RuntimeError:
        return False
    try:
        import neo4j  # noqa: F401
    except ImportError:
        try:
            import mgclient  # noqa: F401
        except ImportError:
            return False
    return True


@pytest.mark.skipif(not _memgraph_available(), reason="memgraph binary or Bolt driver not installed")
def test_memgraph_drop_and_reload_give_the_same_counts(store, run_id, tmp_path, monkeypatch):
    """The CR-7 done-when against Memgraph itself: delete the data, reload from
    helios_index, get the same graph counts."""
    from apps.helios.graph import ontology as ontology_graph
    from apps.helios.graph.bolt import connect
    from apps.helios.tests.test_ontology_graph import sample_graph

    monkeypatch.setenv("HELIOS_GRAPH_DATA_DIR", str(tmp_path / "data"))
    process = memgraph_process.MemgraphProcess()
    process.start()
    try:
        process.wait_until_listening()
        client = connect()
        try:
            ontology_graph.ensure_indexes(client)
            index.ensure_indexes(client)
            ontology_graph.materialise(client, sample_graph(version=ONT))  # has an Item class
            first = project(client, store, run_id)
            counts_1 = index.counts(client, run_id)
            assert counts_1["nodes"]["Asset"] == 3 and counts_1["edges"]["INSTANCE_OF"] == 1
            assert first["entities"]["without_class"] == 6

            index.drop_run(client, run_id)
            assert index.run_state(client, run_id) is None
            project(client, store, run_id)
            assert index.counts(client, run_id) == counts_1
            assert index.run_state(client, run_id)["counts"] == counts_1
        finally:
            client.close()
    finally:
        process.stop()
