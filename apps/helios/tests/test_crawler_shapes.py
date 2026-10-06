"""No retail shapes in crawler code (CG-0): the list of known ones only shrinks.

``baselines/crawler_shapes.json`` lists, per file, the retail terms the code
uses today and how often. A new term, or more uses of a listed one, fails. So
does using fewer: the list must then be regenerated, so it always states what
is left to remove.

    HELIOS_UPDATE_BASELINE=1 python -m pytest apps/helios/tests/test_crawler_shapes.py
"""

import json
import os
from pathlib import Path

import pytest
import shapes

KNOWN = Path(__file__).parent / "baselines" / "crawler_shapes.json"


def _load() -> dict[str, dict[str, int]]:
    return json.loads(KNOWN.read_text())["files"] if KNOWN.exists() else {}


def test_no_new_retail_shapes_in_crawler_code():
    found = shapes.scan()
    if os.environ.get("HELIOS_UPDATE_BASELINE") == "1":
        total = sum(sum(terms.values()) for terms in found.values())
        KNOWN.write_text(
            json.dumps({"total_uses": total, "files": found}, indent=2, sort_keys=True) + "\n"
        )
    known = _load()
    added, removed = [], []
    for file in sorted(set(found) | set(known)):
        now, before = found.get(file, {}), known.get(file, {})
        for term in sorted(set(now) | set(before)):
            if now.get(term, 0) > before.get(term, 0):
                added.append(f"{file}: {term} ({before.get(term, 0)} -> {now[term]})")
            elif now.get(term, 0) < before.get(term, 0):
                removed.append(f"{file}: {term} ({before[term]} -> {now.get(term, 0)})")
    assert not added, (
        "Retail-specific terms were added to crawler code. Put them in configuration "
        "(docs/crawler-configurable.md):\n  " + "\n  ".join(added)
    )
    assert not removed, (
        "Shapes were removed, which is the goal. Record it so they cannot come back:\n"
        "  HELIOS_UPDATE_BASELINE=1 python -m pytest apps/helios/tests/test_crawler_shapes.py\n  "
        + "\n  ".join(removed)
    )


def test_the_scanner_sees_code_and_ignores_comments_and_docstrings():
    source = '''
"""A return of a sale: a docstring may explain the retail example."""
RETURN_CLASS = "Return"  # the comment may say refund
SHAPES = {"REFUND_APPROVED": ("Return", "Customer")}
QUERY = "SELECT sr_ticket_number FROM tpcds.store_returns"

def promote(case_return, sale_key):
    """Docstring: Customer, Store, ticket."""
    for item in store.read("x"):      # ordinary words stay ordinary
        reason = item.reason
    return {"Store": 1, "note": "the store is closed", "phrase": "the item"}
'''
    assert dict(shapes.shapes_in(source)) == {
        "class:Return": 4,  # RETURN_CLASS, "Return" twice, case_return
        "class:Customer": 1,
        "class:Sale": 1,  # sale_key
        "class:Store": 1,  # the exact class name as a string; not ``store`` or "the store ..."
        "claim:REFUND_APPROVED": 1,
        "identifier:sr_ticket_number": 1,
        "identifier:tpcds": 1,
        "identifier:store_returns": 1,
        "preset:the item": 1,  # a default rule's value, word for word
    }


def test_generic_code_is_clean():
    source = '''
def resolve(anchor, lookups):
    for lookup in lookups:
        yield anchor.table, lookup.column, lookup.match
'''
    assert not shapes.shapes_in(source)


@pytest.mark.parametrize("kind", ["class", "claim", "identifier", "preset", "word"])
def test_the_vocabulary_comes_from_the_shipped_retail_files(kind):
    vocabulary = shapes.terms()
    expected = {
        "class": {"Customer", "Return", "Sale", "PartyTo", "Store"},
        "claim": {"REFUND_APPROVED", "PACKAGING_DAMAGED"},
        "identifier": {"store_returns", "c_customer_id", "tpcds", "date_dim"},
        "preset": {"return_authorization", "the item", "RMA number"},
        "word": {"refund", "ticket", "rma", "customer", "sale"},
    }[kind]
    assert expected <= vocabulary[kind]
    assert not {"store", "item", "reason", "date"} & vocabulary["word"]
