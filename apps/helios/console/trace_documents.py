"""The document side of an answer path: what was searched, which documents
were found (grouped by type), and which of their warehouse keys were then used
as filters in the approved query (a "bridge" between documents and tables).

Built from a trace's agent tool spans; pure, so it can be tested without a store.
"""

from __future__ import annotations

from typing import Any

DOCUMENT_TOOLS = frozenset({"search_evidence", "entity_claims"})
MAX_TEXT = 1500


def _key_parts(key: str) -> tuple[str, dict[str, str]]:
    """ "tpcds.customer:c_customer_sk=12" -> ("tpcds.customer", {"c_customer_sk": "12"})."""
    table, _, rendered = key.partition(":")
    pairs = (part.partition("=") for part in rendered.split(",") if "=" in part)
    return table, {name.strip(): value.strip() for name, _, value in pairs}


def _role(column: str) -> str:
    """A key column without its table prefix: sr_customer_sk and c_customer_sk
    are both "customer_sk", which is how a fact table refers to a dimension."""
    return column.partition("_")[2] or column


def _filters(arguments: dict[str, Any]) -> list[tuple[str, set[str]]]:
    found = []
    for item in arguments.get("filters") or []:
        if not isinstance(item, dict):
            continue
        field = item.get("column") or item.get("field")
        raw = item.get("value", item.get("values"))
        values = raw if isinstance(raw, list) else [raw]
        if isinstance(field, str) and values:
            found.append((field, {str(v) for v in values if v is not None}))
    return found


def document_evidence(agent_tools: list[Any]) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """(documents, edges) for the answer path; (None, []) when no document tool ran."""
    searches: list[dict[str, Any]] = []
    groups: dict[str, dict[str, Any]] = {}
    query_filters: list[tuple[str, set[str]]] = []

    def add(kind: str, search_id: str, passage: dict[str, Any]) -> None:
        group = groups.setdefault(
            kind, {"id": f"documents:{kind}", "type": kind, "search_ids": [], "passages": []}
        )
        if search_id not in group["search_ids"]:
            group["search_ids"].append(search_id)
        if all(p["id"] != passage["id"] for p in group["passages"]):
            group["passages"].append(passage)

    for span in agent_tools:
        arguments = span.input.get("parsed_arguments", {}) if isinstance(span.input, dict) else {}
        arguments = arguments if isinstance(arguments, dict) else {}
        output = span.output if isinstance(span.output, dict) else {}
        if span.name not in DOCUMENT_TOOLS:
            if isinstance(output.get("sql"), str):  # the query that ran
                query_filters = _filters(arguments)
            continue
        if span.name == "search_evidence":
            segments = [s for s in output.get("segments") or [] if isinstance(s, dict)]
            searches.append(
                {
                    "id": span.id,
                    "tool": span.name,
                    "query": str(arguments.get("query") or ""),
                    "status": span.status,
                    "result_count": len(segments),
                }
            )
            for segment in segments:
                add(
                    str(segment.get("segment_type") or "document"),
                    span.id,
                    {
                        "id": str(segment.get("segment_id")),
                        "asset_id": segment.get("asset_id"),
                        "text": str(segment.get("text") or "")[:MAX_TEXT],
                        "locator": segment.get("locator") or {},
                        "relevance": segment.get("relevance"),
                        "entities": [e for e in segment.get("entities") or [] if isinstance(e, dict)],
                    },
                )
        else:
            claims = [c for c in output.get("claims") or [] if isinstance(c, dict)]
            matched = [e for e in output.get("entities") or [] if isinstance(e, dict)]
            searches.append(
                {
                    "id": span.id,
                    "tool": span.name,
                    "query": str(arguments.get("entity") or ""),
                    "status": span.status,
                    "result_count": int(output.get("claims_total") or len(claims)),
                }
            )
            for index, claim in enumerate(claims):
                for number, passage in enumerate(claim.get("evidence") or []):
                    add(
                        "claim",
                        span.id,
                        {
                            "id": f"{span.id}:{index}:{number}",
                            "asset_id": passage.get("asset_id"),
                            "text": str(passage.get("excerpt") or "")[:MAX_TEXT],
                            "locator": passage.get("locator") or {},
                            "relevance": None,
                            "claim": claim.get("predicate"),
                            "entities": matched,
                        },
                    )
    if not searches:
        return None, []

    bridges: list[dict[str, Any]] = []
    for group in groups.values():
        group["count"] = len(group["passages"])
        seen: set[tuple[str, str]] = set()
        for passage in group["passages"]:
            for entity in passage["entities"]:
                for key in entity.get("keys") or []:
                    _table, columns = _key_parts(str(key))
                    for field, values in query_filters:
                        column = field.rsplit(".", 1)[-1]
                        value = next(
                            (
                                v
                                for name, v in columns.items()
                                if v in values and (name == column or _role(name) == _role(column))
                            ),
                            None,
                        )
                        if value is None or (field, value) in seen:
                            continue
                        seen.add((field, value))
                        bridges.append(
                            {
                                "id": f"{group['id']}:{field}={value}",
                                "group_id": group["id"],
                                "passage_id": passage["id"],
                                "key": key,
                                "field": field,
                                "value": value,
                                "entity": {
                                    "class": entity.get("class"),
                                    "name": entity.get("name"),
                                },
                            }
                        )

    edges: list[dict[str, str]] = []
    for search in searches:
        edges.append(
            {
                "id": f"assistant-{search['id']}",
                "source": "assistant",
                "target": search["id"],
                "type": "searched_documents",
            }
        )
    for group in groups.values():
        for search_id in group["search_ids"]:
            edges.append(
                {
                    "id": f"{search_id}-{group['id']}",
                    "source": search_id,
                    "target": group["id"],
                    "type": "found_passages",
                }
            )
        edges.append(
            {
                "id": f"{group['id']}-answer",
                "source": group["id"],
                "target": "answer",
                "type": "supported_answer",
            }
        )
        if any(b["group_id"] == group["id"] for b in bridges):
            edges.append(
                {
                    "id": f"{group['id']}-query",
                    "source": group["id"],
                    "target": "query",
                    "type": "key_used_in_query",
                }
            )
    if not groups:  # searched and found nothing: still show where the path ended
        edges += [
            {
                "id": f"{s['id']}-answer",
                "source": s["id"],
                "target": "answer",
                "type": "supported_answer",
            }
            for s in searches
        ]
    return {"searches": searches, "groups": list(groups.values()), "bridges": bridges}, edges
