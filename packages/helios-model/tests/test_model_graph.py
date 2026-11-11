"""helios_model.graph: a model's graph from its artifacts, with no organizations or
permissions, on any storage (burndown 5.3)."""
import dataclasses

import pytest

from helios_cortex.artifact_storage import LocalStorage
from helios_cortex.artifacts import ArtifactStore
from helios_cortex.domain import DataSourceReference, Model
from helios_cortex.ossie import dump_yaml
from helios_model import review
from helios_model.build import build
from helios_model.graph import ArtifactGraphRepository, graph_response, navigation_graph_response, project_graph_lens

RUN = "20261110T120000Z"
MODEL = Model("sales", "Sales", (DataSourceReference("warehouse", ("shop.orders", "shop.customers")),),
              description="Orders by customer", discovery_run_ids=(RUN,))


def proposal():
    def field(column, role, kind="INTEGER"):
        return {"column": column, "type": kind, "name": column, "role": role, "description": "", "confidence": 0.9}

    return {
        "model_id": "sales",
        "datasets": [
            {"table": "shop.orders", "name": "Orders", "kind": "fact", "description": "Orders", "confidence": 0.9,
             "primary_key": ["o_order_sk"], "fields": [field("o_order_sk", "identifier"),
                                                       field("o_customer_sk", "foreign_key"),
                                                       field("o_amount", "measure", "DOUBLE")]},
            {"table": "shop.customers", "name": "Customers", "kind": "dimension", "description": "", "confidence": 0.9,
             "primary_key": ["c_customer_sk"], "fields": [field("c_customer_sk", "identifier")]},
        ],
        "relationships": [{"from": "shop.orders", "from_column": "o_customer_sk", "to": "shop.customers",
                           "to_column": "c_customer_sk", "source": "profile", "accepted": True, "confidence": 0.95}],
        "metrics": [{"name": "Revenue", "dataset": "shop.orders", "expression": "SUM(o_amount)", "confidence": 0.8}],
        "glossary_terms": [{"name": "Revenue", "definition": "Money in", "columns": ["shop.orders.o_amount"],
                            "confidence": 0.8}],
    }


class ObjectLike:
    """A directory that behaves like object storage: the same operations and no local
    paths, so anything that still reaches for one fails."""

    def __init__(self, root):
        self._directory = LocalStorage(root)
        self.url = "memory://object-like"

    def local_path(self, key):
        return None

    def __getattr__(self, name):            # read_bytes, write_bytes, create, exists, list
        return getattr(self._directory, name)


@pytest.fixture(params=["local", "object storage"])
def artifacts(request, tmp_path):
    storage = LocalStorage(tmp_path) if request.param == "local" else ObjectLike(tmp_path)
    return ArtifactStore(storage)


def test_a_model_with_no_artifacts_is_its_configuration(artifacts):
    graph = ArtifactGraphRepository(artifacts).graph_for_model(MODEL)
    assert [(n.id, n.kind, n.status) for n in graph.nodes] == [("model:sales", "domain", "configured"),
                                                               ("datasource:warehouse", "data_source", "configured")]
    assert graph.nodes[1].metadata == {"selected_assets": ["shop.orders", "shop.customers"]}


def test_the_newest_proposal_then_the_published_model(artifacts):
    repository = ArtifactGraphRepository(artifacts)
    older = {**proposal(), "metrics": []}
    artifacts.write_proposal("sales", "20261101T000000Z", older)
    artifacts.write_proposal("sales", RUN, proposal())
    proposed = repository.graph_for_model(MODEL)
    assert {n.status for n in proposed.nodes if n.kind != "domain" and n.kind != "data_source"} == {"proposed"}
    assert "metric:Revenue" in {n.id for n in proposed.nodes}                   # the newer run's proposal
    assert repository.detail_for_element(MODEL, "dataset:shop.orders")["primary_key"] == ["o_order_sk"]

    document, problems = build(proposal(), "sales", model_id="sales", run_id=RUN)
    assert problems == []
    artifacts.write_published_ossie("sales", document, dump_yaml(document), {"run_id": RUN})
    published = repository.graph_for_model(MODEL)
    kinds = sorted({n.kind for n in published.nodes})
    assert kinds == ["attribute", "data_source", "dataset", "domain", "metric"]
    assert {n.status for n in published.nodes if n.kind == "dataset"} == {"published"}
    [join] = [e for e in published.edges if e.id.startswith("relationship:")]
    assert (join.source, join.target) == ("dataset:orders", "dataset:customers")
    detail = repository.detail_for_element(MODEL, "attribute:orders:o_customer_sk")
    assert (detail["physical_identity"], detail["semantic_role"]) == ("shop.orders.o_customer_sk", "foreign_key")


def test_a_run_under_review_shows_each_decision(artifacts):
    repository = ArtifactGraphRepository(artifacts)
    runs = artifacts.runs
    runs.write(RUN, "propose", proposal())
    decisions = review.empty(RUN)
    review.decide(decisions, "datasets", "shop.orders", "accept")
    review.decide(decisions, "metrics", "shop.orders::Revenue", "reject", note="double counts refunds")
    runs.write(RUN, "review", decisions)

    graph = repository.review_graph_for_model(MODEL, RUN)
    status = {n.id: n.status for n in graph.nodes}
    assert (status["dataset:shop.orders"], status["metric:Revenue"], status["dataset:shop.customers"]) == (
        "approved", "rejected", "needs_review")
    assert next(n for n in graph.nodes if n.id == "metric:Revenue").metadata["review_note"] == "double counts refunds"
    assert repository.review_detail_for_element(MODEL, RUN, "concept:Revenue")["mapped_attributes"] == [
        "shop.orders.o_amount"]
    for run_id in ("20990101T000000Z", "../escape"):              # a run that is not the model's
        with pytest.raises(FileNotFoundError):
            repository.review_graph_for_model(MODEL, run_id)


def test_responses_lenses_and_navigation_carry_no_ownership(artifacts):
    artifacts.write_proposal("sales", RUN, proposal())
    graph = ArtifactGraphRepository(artifacts).graph_for_model(MODEL)
    assert {f.name for f in dataclasses.fields(graph.nodes[0])}.isdisjoint({"organization_id", "permitted_actions"})

    response = graph_response(graph)
    assert set(response) == {"model_id", "nodes", "edges", "summary"}
    assert set(response["nodes"][0]) == {"id", "kind", "label", "status", "confidence", "evidence", "metadata"}

    physical = project_graph_lens(graph, "physical")
    assert {n.kind for n in physical.nodes} == {"domain", "data_source", "dataset", "attribute"}

    navigation = navigation_graph_response(graph, depth=1)
    assert navigation["navigation"]["graph_node_count"] == len(graph.nodes)
    assert {n["id"] for n in navigation["nodes"]} >= {"model:sales", "dataset:shop.orders"}
    assert "attribute:shop.orders:o_amount" not in {n["id"] for n in navigation["nodes"]}   # attributes on request
