"""Relationship candidates the proposer rejected are not work waiting for the reviewer."""

from helios_core import review as rv

PROPOSAL = {
    "datasets": [{"table": "s.a", "fields": [{"column": "id"}]}, {"table": "s.b", "fields": []}],
    "relationships": [
        {"from": "s.a", "from_column": "b_id", "to": "s.b", "to_column": "id",
         "accepted": True, "confidence": 0.95},
        {"from": "s.a", "from_column": "kind", "to": "s.b", "to_column": "id",
         "accepted": False, "confidence": 0.9, "source": "llm_rejected"},
    ],
    "metrics": [],
    "glossary_terms": [],
}
CONFIRMED, MODEL_REJECTED = (rv.relationship_id(r) for r in PROPOSAL["relationships"])


def test_bulk_accept_leaves_nothing_pending():
    review = rv.empty("run")
    rv.bulk_accept(review, PROPOSAL, 0.0)
    assert MODEL_REJECTED not in review["relationships"]  # no decision is invented
    counts = rv.summary(review, PROPOSAL)["relationships"]
    assert counts == {"accept": 1, "reject": 1, "edit": 0, "pending": 0, "total": 2}
    assert sum(section["pending"] for section in rv.summary(review, PROPOSAL).values()) == 0


def test_an_undecided_confirmed_relationship_is_still_pending():
    counts = rv.summary(rv.empty("run"), PROPOSAL)["relationships"]
    assert counts["pending"] == 1 and counts["reject"] == 1


def test_the_reviewer_can_overturn_a_model_rejection():
    review = rv.empty("run")
    rv.decide(review, "relationships", MODEL_REJECTED, "accept")
    rel = PROPOSAL["relationships"][1]
    assert rv.effective_decision(review, "relationships", MODEL_REJECTED, rel) == "accept"
    assert rv.summary(review, PROPOSAL)["relationships"]["accept"] == 1
    kept = [rv.relationship_id(r) for r in rv.apply(PROPOSAL, review, accept_pending=True)["relationships"]]
    assert MODEL_REJECTED in kept


def test_counts_agree_with_what_publishing_keeps():
    review = rv.empty("run")
    rv.bulk_accept(review, PROPOSAL, 0.0)
    kept = [rv.relationship_id(r) for r in rv.apply(PROPOSAL, review)["relationships"]]
    assert kept == [CONFIRMED]
