"""Source mappings as versioned documents with an API (CG-6)."""

import copy
from pathlib import Path

import pytest
import yaml
from helios_core.index import mappings as stored
from helios_core.ontology.mapping import (
    SourceMapping,
    load_mapping,
    mapping_problems,
    resolution_config,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SHIPPED_FILE = REPO_ROOT / "ontology/mappings/ossie/tpcds.yaml"
SHIPPED = load_mapping(SHIPPED_FILE)
OSSIE = yaml.safe_load((REPO_ROOT / "models/published/tpcds.ossie.yaml").read_text())
DOCUMENT = SHIPPED.model_dump(mode="json", by_alias=True)
CLASSES = (
    {e.class_name for e in SHIPPED.entities}
    | {r.edge for r in SHIPPED.relationships}
    | {c.class_name for c in SHIPPED.concepts}
    | {r.edge for a in SHIPPED.anchors for r in a.related}
)
ALICE = "cloudera-workbench:alice"


def changed(**edits):
    document = copy.deepcopy(DOCUMENT)
    for path, value in edits.items():
        target = document
        *parents, last = path.split("__")
        for part in parents:
            target = target[int(part)] if part.isdigit() else target[part]
        target[int(last) if last.isdigit() else last] = value
    return document


# --- the store -------------------------------------------------------------------------



def test_a_stored_mapping_gives_the_crawler_exactly_what_the_file_did(index):
    record, created = stored.save(index, SHIPPED, ALICE, "imported")
    assert created and record.version == 1
    assert (record.model, record.ontology_version) == (SHIPPED.model, SHIPPED.ontology_version)
    assert stored.mapping_of(stored.get(index, 1)) == SHIPPED
    assert resolution_config(stored.mapping_of(record)) == resolution_config(SHIPPED)


def test_versions_are_immutable_and_activation_is_per_model(index):
    first, _ = stored.save(index, SHIPPED, ALICE)
    same, created = stored.save(index, SourceMapping.model_validate(DOCUMENT), "bob")
    assert (same.version, created) == (1, False)  # identical content is not a new version

    stricter = SourceMapping.model_validate(changed(resolution__thresholds__alias_min_score=0.95))
    second, _ = stored.save(index, stricter, ALICE, "stricter aliases")
    other = SourceMapping.model_validate(changed(model="claims.ossie.yaml"))
    third, _ = stored.save(index, other, ALICE)
    assert [r.version for r in stored.versions(index)] == [1, 2, 3]
    assert stored.active(index) == {}

    ticks = iter(f"2026-01-01T00:00:0{i}Z" for i in range(9))
    clock = lambda: next(ticks)  # noqa: E731
    stored.activate(index, 1, ALICE, clock)
    stored.activate(index, 3, ALICE, clock)
    stored.activate(index, 2, ALICE, clock)
    active = stored.active(index)
    assert {model: r.version for model, r in active.items()} == {
        "claims.ossie.yaml": 3,
        SHIPPED.model: 2,
    }
    assert stored.mapping_of(active[SHIPPED.model]).resolution.thresholds.alias_min_score == 0.95
    with pytest.raises(stored.UnknownMappingVersion):
        stored.activate(index, 9, ALICE)


def test_the_shipped_mapping_is_valid():
    assert mapping_problems(SHIPPED, OSSIE, CLASSES) == []


@pytest.mark.parametrize(
    ("edits", "expected"),
    [
        ({"entities__0__ossie_element": "dim_customer"}, "table 'dim_customer' is not in the semantic model"),
        ({"entities__0__identifiers__secondary": ["c_loyalty_id"]}, "column 'c_loyalty_id' is not in table 'customer'"),
        ({"entities__0__identifiers__alias_templates": ["{c_nickname} {c_last_name}"]}, "column 'c_nickname' is not in table 'customer'"),
        ({"entities__0__identifiers__primary": []}, "needs at least one primary key column"),
        ({"entities__0__identifiers__display": ["c_first_name; DROP TABLE x"]}, "is not a plain column name"),
        ({"entities__0__class": "Shopper"}, "class Shopper is not in the ontology"),
        ({"entities__1__class": "Customer"}, "class Customer is mapped more than once"),
        ({"relationships__0__ossie_relationship": "a__b__c"}, "is not a relationship in the semantic model"),
        ({"relationships__0__edge": "Buys"}, "relationship type Buys is not in the ontology"),
        ({"resolution__thresholds__alias_min_score": 1.5}, "alias_min_score: must be between 0 and 1"),
    ],
)
def test_problems_name_what_is_wrong(edits, expected):
    assert SHIPPED.entities[0].class_name == "Customer"
    problems = mapping_problems(SourceMapping.model_validate(changed(**edits)), OSSIE, CLASSES)
    assert any(expected in p for p in problems), problems


def test_checks_that_need_the_model_or_ontology_are_skipped_without_them():
    renamed = SourceMapping.model_validate(changed(entities__0__ossie_element="dim_customer"))
    assert mapping_problems(renamed) == []


def test_the_shipped_anchor_is_valid_and_its_mistakes_are_named():
    classes = CLASSES | {"ReturnOf"}
    assert [a.class_name for a in SHIPPED.anchors] == ["Return"]
    assert mapping_problems(SHIPPED, OSSIE, classes) == []

    def problems(**edits):
        return mapping_problems(SourceMapping.model_validate(changed(**edits)), OSSIE, classes)

    def has(found, text):
        assert any(text in p for p in found), found

    has(problems(anchors__0__class="Shipment"), "class Shipment has no entity mapping")
    has(problems(anchors__0__lookups__0__column="sr_receipt"), "column 'sr_receipt' is not in table 'store_returns'")
    has(problems(anchors__0__lookups__0__identifies="Customer"), "identifies Customer, which is neither")
    has(problems(anchors__0__joins__0__column="sr_buyer_sk"), "column 'sr_buyer_sk' is not in table 'store_returns'")
    has(problems(anchors__0__joins__0__edge="Buys"), "relationship type Buys is not in the ontology")
    has(problems(anchors__0__joins__0__class="Sale"), "needs a single-column key")
    has(problems(anchors__0__colocated__0__via="Store"), "Brand is in table 'item', not Store's table 'store'")
    has(problems(anchors__0__colocated__0__via="Promotion"), "not one of the anchor's joins")
    has(problems(anchors__0__date__table="calendar"), "table 'calendar' is not in the semantic model")
    has(problems(anchors__0__date__value=None), "a date table needs its key and value columns")
    has(problems(anchors__0__related__0__join_on=[["sr_ticket_number", "ss_receipt"]]), "column 'ss_receipt' is not in table 'store_sales'")
    has(problems(anchors__0__related__0__edge="Undoes"), "relationship type Undoes is not in the ontology")
    doubled = changed()
    doubled["anchors"].append(doubled["anchors"][0])
    has(mapping_problems(SourceMapping.model_validate(doubled), OSSIE, classes), "is an anchor more than once")


def test_a_bare_on_key_is_rejected_because_yaml_reads_it_as_true():
    import yaml as yaml_module
    from pydantic import ValidationError

    document = yaml_module.safe_load("class: Sale\non: [[a, b]]\nedge: ReturnOf\n")
    assert True in document  # what YAML made of "on"
    from helios_core.ontology.mapping import RelatedRecord

    with pytest.raises(ValidationError):
        RelatedRecord.model_validate(document)
