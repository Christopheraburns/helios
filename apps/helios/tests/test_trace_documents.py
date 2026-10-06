"""The document lane of an answer path, built from a trace's tool spans."""

from types import SimpleNamespace

from apps.helios.console.trace_documents import document_evidence


def span(span_id, name, arguments, output, status="success"):
    return SimpleNamespace(
        id=span_id, name=name, status=status, input={"parsed_arguments": arguments}, output=output
    )


def passage(segment_id, kind, customer_sk, name):
    return {
        "segment_id": segment_id,
        "asset_id": f"asset-{segment_id}",
        "segment_type": kind,
        "locator": {"part": "body"},
        "text": "The box was crushed.",
        "relevance": 0.58,
        "entities": [
            {
                "entity_id": f"e-{customer_sk}",
                "class": "Customer",
                "name": name,
                "keys": [f"tpcds.customer:c_customer_sk={customer_sk}"],
            },
            {
                "entity_id": "r",
                "class": "Return",
                "name": "RMA-1",
                "keys": ["tpcds.store_returns:sr_item_sk=7,sr_ticket_number=88705"],
            },
        ],
    }


SEARCH = span(
    "s1",
    "search_evidence",
    {"query": "damaged packaging"},
    {
        "segments": [
            passage("p1", "email", 88705, "Barbara Clark"),
            passage("p2", "email", 111, "Someone Else"),
            passage("p3", "chat", 27760, "Rebecca Cochran"),
        ]
    },
)


def test_documents_are_grouped_by_type_and_keys_used_as_filters_become_bridges():
    failed = span("q0", "run_query", {"filters": [{"column": "x", "value": [111]}]}, {"error": "bad"})
    query = span(
        "q1",
        "run_query",
        {"filters": [{"column": "tpcds.store_returns.sr_customer_sk", "op": "IN", "value": [88705, 27760]}]},
        {"sql": "SELECT 1", "rows": []},
    )
    documents, edges = document_evidence([SEARCH, failed, query])

    assert documents["searches"] == [
        {"id": "s1", "tool": "search_evidence", "query": "damaged packaging", "status": "success", "result_count": 3}
    ]
    assert [(g["id"], g["count"]) for g in documents["groups"]] == [
        ("documents:email", 2),
        ("documents:chat", 1),
    ]
    # Matched through the fact table's foreign key; the unrelated ticket number
    # 88705 and the failed query's filter are not bridges.
    assert [(b["group_id"], b["passage_id"], b["field"], b["value"], b["entity"]["name"]) for b in documents["bridges"]] == [
        ("documents:email", "p1", "tpcds.store_returns.sr_customer_sk", "88705", "Barbara Clark"),
        ("documents:chat", "p3", "tpcds.store_returns.sr_customer_sk", "27760", "Rebecca Cochran"),
    ]
    assert {(e["source"], e["target"], e["type"]) for e in edges} == {
        ("assistant", "s1", "searched_documents"),
        ("s1", "documents:email", "found_passages"),
        ("s1", "documents:chat", "found_passages"),
        ("documents:email", "answer", "supported_answer"),
        ("documents:chat", "answer", "supported_answer"),
        ("documents:email", "query", "key_used_in_query"),
        ("documents:chat", "query", "key_used_in_query"),
    }


def test_no_bridge_without_a_matching_filter_and_nothing_without_a_document_tool():
    query = span("q1", "run_query", {"filters": []}, {"sql": "SELECT 1"})
    documents, edges = document_evidence([SEARCH, query])
    assert documents["bridges"] == []
    assert all(e["type"] != "key_used_in_query" for e in edges)
    assert document_evidence([query]) == (None, [])


def test_claims_and_empty_searches_are_shown():
    claims = span(
        "c1",
        "entity_claims",
        {"entity": "Barbara Clark"},
        {
            "entities": [{"class": "Customer", "name": "Barbara Clark", "keys": ["tpcds.customer:c_customer_sk=88705"]}],
            "claims_total": 4,
            "claims": [
                {"predicate": "REFUND_REQUESTED", "evidence": [{"asset_id": "a", "locator": {}, "excerpt": "Refund please."}]}
            ],
        },
    )
    empty = span("s2", "search_evidence", {"query": "unicorns"}, {"segments": []})
    documents, edges = document_evidence([claims, empty])
    assert [(s["tool"], s["result_count"]) for s in documents["searches"]] == [
        ("entity_claims", 4),
        ("search_evidence", 0),
    ]
    [group] = documents["groups"]
    assert (group["type"], group["passages"][0]["claim"], group["passages"][0]["text"]) == (
        "claim",
        "REFUND_REQUESTED",
        "Refund please.",
    )
    assert ("documents:claim", "answer") in {(e["source"], e["target"]) for e in edges}
