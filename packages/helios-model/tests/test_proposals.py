"""Proposals: the typed draft model and the decisions on it (burndown 5.4)."""
import pytest

from helios_model import Proposals, ProposedDataset, ProposedRelationship
from helios_model.propose import ModelProposer

RUN = "20261110T120000Z"


def proposal():
    def field(column, role, rationale=None):
        return {"column": column, "type": "INTEGER", "name": column.title(), "role": role, "description": "",
                "confidence": 0.9, "refers_to": None, "glossary_terms": [], "proposed_term": None,
                **({"rationale": rationale} if rationale else {})}

    return {
        "model_id": "sales",
        "llm": {"provider": "scripted", "model": "m", "calls": 3},
        "datasets": [
            {"table": "shop.orders", "name": "Orders", "kind": "fact", "description": "One row per order.",
             "confidence": 0.9, "rationale": "Many rows with measures and keys.", "primary_key": ["o_id"],
             "fields": [field("o_id", "identifier", "Unique and never null."), field("o_customer", "foreign_key"),
                        field("o_amount", "measure")]},
            {"table": "shop.customers", "name": "Customers", "kind": "dimension", "description": "",
             "confidence": 0.4, "primary_key": ["c_id"], "fields": [field("c_id", "identifier")]},
        ],
        "relationships": [
            {"from": "shop.orders", "from_column": "o_customer", "to": "shop.customers", "to_column": "c_id",
             "source": "profile", "accepted": True, "confidence": 0.95, "match_ratio": 1.0,
             "rationale": "Every value is found."},
            # written before rationale existed: only the LLM's reason
            {"from": "shop.orders", "from_column": "o_amount", "to": "shop.customers", "to_column": "c_id",
             "source": "llm_rejected", "accepted": False, "confidence": 0.7, "reason": "Small integers overlap."},
        ],
        "metrics": [{"name": "Revenue", "dataset": "shop.orders", "expression": "SUM(o_amount)",
                     "source": "derived", "confidence": 0.8, "rationale": "Money in."}],
        "glossary_terms": [{"name": "Revenue", "definition": "Money in", "columns": ["shop.orders.o_amount"],
                            "confidence": 0.8}],
    }


def test_elements_are_typed_with_confidence_rationale_and_decision():
    proposals = Proposals(proposal(), run_id=RUN, model_id="sales")
    [orders, customers] = proposals.datasets
    assert isinstance(orders, ProposedDataset)
    assert (orders.id, orders.label, orders.kind, orders.primary_key) == ("shop.orders", "Orders", "fact", ("o_id",))
    assert (orders.confidence, orders.rationale, orders.decision) == (0.9, "Many rows with measures and keys.", "pending")
    assert [f.id for f in orders.fields] == ["shop.orders.o_id", "shop.orders.o_customer", "shop.orders.o_amount"]
    assert (orders.fields[0].rationale, orders.fields[1].rationale) == ("Unique and never null.", None)
    assert customers.rationale is None                       # the LLM gave none

    found, coincidence = proposals.relationships
    assert isinstance(found, ProposedRelationship)
    assert (found.from_table, found.to_column, found.source, found.match_ratio) == (
        "shop.orders", "c_id", "profile", 1.0)
    assert coincidence.rationale == "Small integers overlap."      # the reason, from an older proposal
    assert coincidence.decision == "reject"                        # the LLM turned it down
    assert [m.expression for m in proposals.metrics] == ["SUM(o_amount)"]
    assert proposals.glossary_terms[0].columns == ("shop.orders.o_amount",)
    assert len(proposals) == 2 + 4 + 2 + 1 + 1 == len(list(proposals))
    assert proposals.llm["calls"] == 3


def test_decisions_shape_what_is_reviewed():
    proposals = Proposals(proposal(), run_id=RUN, model_id="sales")
    orders = proposals.get("datasets", "shop.orders")
    proposals.accept(orders)
    proposals.edit(("fields", "shop.orders.o_amount"), {"name": "Amount paid"}, note="business name")
    proposals.accept(("fields", "shop.orders.o_id"))
    proposals.reject(proposals.get("fields", "shop.orders.o_customer"), note="internal")
    proposals.accept(proposals.metrics[0])

    edited = proposals.get("fields", "shop.orders.o_amount")
    assert (edited.decision, edited.note, dict(edited.overrides)) == ("edit", "business name", {"name": "Amount paid"})
    assert edited.label == "O_Amount"                       # the element as proposed; overrides beside it
    assert orders.decision == "pending"                     # an element is a snapshot
    assert proposals.summary()["fields"] == {"accept": 1, "reject": 1, "edit": 1, "pending": 1, "total": 4}

    reviewed = proposals.reviewed()
    [kept] = reviewed["datasets"]
    assert [(f["column"], f["name"]) for f in kept["fields"]] == [("o_id", "O_Id"), ("o_amount", "Amount paid")]
    assert reviewed["relationships"] == []                  # customers is still pending
    assert [m["name"] for m in reviewed["metrics"]] == ["Revenue"]
    assert proposals.proposal == proposal()                 # the proposal itself never changes


def test_accept_all_respects_confidence_and_the_llms_rejections():
    proposals = Proposals(proposal(), run_id=RUN, model_id="sales")
    proposals.reject(("glossary_terms", "Revenue"))
    assert proposals.accept_all(min_confidence=0.5) == 1 + 4 + 1 + 1     # not customers (0.4), not the term
    assert {p.id for p in proposals.pending()} == {"shop.customers"}
    assert proposals.relationships[1].decision == "reject"
    proposals.clear(("glossary_terms", "Revenue"))
    assert proposals.get("glossary_terms", "Revenue").decision == "pending"
    proposals.clear()
    assert len(proposals.pending()) == len(proposals) - 1                 # the LLM's rejection stays


def test_a_review_continues_and_mistakes_are_refused():
    first = Proposals(proposal(), run_id=RUN, model_id="sales")
    first.accept(("datasets", "shop.orders"))
    first.mark_reviewed("alice")
    again = Proposals(proposal(), run_id=RUN, model_id="sales", review=first.review)
    assert again.get("datasets", "shop.orders").decision == "accept"
    assert again.review["reviewed_by"] == "alice"

    with pytest.raises(KeyError):
        again.accept(("datasets", "shop.missing"))
    with pytest.raises(ValueError, match="unknown section"):
        again.get("tables", "shop.orders")
    with pytest.raises(ValueError, match="at least one change"):
        again.edit(("datasets", "shop.orders"), {})
    element = again.get("datasets", "shop.orders")
    with pytest.raises(TypeError):
        element.element["name"] = "changed"                 # read-only


class Scripted:
    provider, model, calls = "scripted", "m", 0

    def __init__(self, answers):
        self.answers = answers

    def complete_json(self, system, user):
        return self.answers.pop(0)


def test_the_proposer_keeps_the_llms_rationale_and_writes_the_profilers():
    harvest = {"tables": [{"database": "shop", "table": "orders", "columns": [{"name": "o_id", "type": "INTEGER"}]}],
               "queries": {"statements": []}}
    profile = {"tables": {}, "relationships": [
        {"from": "shop.orders", "from_column": "o_id", "to": "shop.ids", "to_column": "id",
         "distinct_values": 4, "match_ratio": 0.98}],
        "suggested_relationships": [
        {"from": "shop.orders", "from_column": "o_id", "to": "shop.other", "to_column": "id",
         "distinct_values": 4, "match_ratio": 1.0}]}
    llm = Scripted([
        {"name": "Orders", "kind": "fact", "rationale": " Events with keys. ",
         "fields": [{"column": "o_id", "role": "identifier", "rationale": ""}]},
        {"decisions": [{"index": 0, "accept": False, "confidence": 0.8, "reason": "Coincidence."}]},
    ])
    result = ModelProposer(llm).run(harvest, profile)
    [dataset] = result["datasets"]
    assert (dataset["rationale"], dataset["fields"][0]["rationale"]) == ("Events with keys.", None)
    found, decided = result["relationships"]
    assert found["rationale"] == ("98% of the 4 distinct values of shop.orders.o_id are found in shop.ids.id, "
                                  "and the column name points at that table.")
    assert (decided["rationale"], decided["reason"]) == ("Coincidence.", "Coincidence.")
