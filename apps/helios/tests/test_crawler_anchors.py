"""Record anchors (CG-4): the case query is built from the mapping, for any domain.

The retail anchor is covered by the same-output tests. Here the same code runs
against a warehouse it has never seen, with different classes, a single-column
key, a date in its own column, text lookups and no related record or colocated
class, configured only through a mapping.
"""

from datetime import date
from types import SimpleNamespace

import duckdb
import pytest
from apps.helios.crawler.anchors import (
    Constraints,
    LookupValue,
    build_query,
    decide_rows,
    gather_constraints,
    lookups_for,
    plans,
    resolve_cluster,
    row_match,
)
from helios_core.ontology.mapping import SourceMapping, resolution_config
from test_crawler_resolution import CONFIG as RETAIL

CLINIC = {
    "model": "clinic.ossie.yaml",
    "ontology_version": "clinic@1.0.0",
    "database": "clinic",
    "entities": [
        {"ossie_element": "visits", "class": "Visit", "identifiers": {"primary": ["visit_id"]}},
        {"ossie_element": "patients", "class": "Patient", "identifiers": {"primary": ["patient_id"], "display": ["full_name"]}},
        {"ossie_element": "doctors", "class": "Clinician", "identifiers": {"primary": ["doctor_id"], "display": ["doctor_name"]}},
    ],
    "anchors": [
        {
            "class": "Visit",
            "lookups": [
                {"column": "visit_code", "match": "equals", "type": "string"},
                {"column": "visit_code", "match": "ends_with", "type": "string", "source_columns": ["visit_code_tail"]},
            ],
            "joins": [
                {"column": "patient_id", "class": "Patient", "edge": "AttendedBy"},
                {"column": "doctor_id", "class": "Clinician", "edge": "SeenBy"},
            ],
            "date": {"column": "visit_date"},
            "row_limit": 3,
            "min_constraint_kinds": 2,
        }
    ],
}
CONFIG = resolution_config(SourceMapping.model_validate(CLINIC))


@pytest.fixture(scope="module")
def clinic():
    con = duckdb.connect()
    con.execute("CREATE SCHEMA clinic")
    con.execute(
        "CREATE TABLE clinic.visits (visit_id BIGINT, visit_code VARCHAR, patient_id BIGINT, "
        "doctor_id BIGINT, visit_date DATE)"
    )
    con.executemany(
        "INSERT INTO clinic.visits VALUES (?, ?, ?, ?, ?)",
        [
            (1, "V-2024-0001", 10, 7, "2024-03-01"),
            (2, "V-2024-0002", 10, 8, "2024-03-09"),
            (3, "V-2024-0003", 11, 7, "2024-03-09"),
            (4, "V-2024-0004", 10, 7, "2024-04-20"),
        ],
    )
    return con


def reading(segment="s1", candidates=(), lookups=(), day=None):
    return SimpleNamespace(
        mention=SimpleNamespace(segment_id=segment),
        candidates=[SimpleNamespace(instance=i, class_name=c, tier=t) for i, c, t in candidates],
        lookups=list(lookups),
        date=day,
    )


def test_the_query_is_built_from_the_mapping_alone():
    [plan] = plans(CONFIG)
    assert plan.extra_columns == ("visit_code",)  # a lookup column the key and joins do not select
    constraints = Constraints()
    constraints.instances["Patient"] = {"clinic.patients:patient_id=10"}
    constraints.instances["Clinician"] = {"clinic.doctors:doctor_id=7"}
    constraints.lookups[("visit_code", "ends_with")] = {"0004"}
    sql, params = build_query(plan, constraints)
    assert sql == (
        "SELECT r.visit_id, r.patient_id, r.doctor_id, r.visit_date, r.visit_code "
        "FROM clinic.visits r "
        "WHERE r.doctor_id IN (?) AND r.patient_id IN (?) AND r.visit_code LIKE ? LIMIT 4"
    )
    assert params == [7, 10, "%0004"]


def test_one_row_identifies_the_case_and_dates_settle_several(clinic):
    [plan] = plans(CONFIG)
    patient, doctor = ("clinic.patients:patient_id=10", "Patient", "alias"), ("clinic.doctors:doctor_id=7", "Clinician", "alias")

    match, constraints, queried = resolve_cluster(plan, [reading(candidates=[patient])], clinic.cursor())
    assert (match, queried) == (None, False)  # one kind of evidence is not enough to ask

    match, _, queried = resolve_cluster(plan, [reading(candidates=[patient, doctor])], clinic.cursor())
    assert queried and match is None  # visits 1 and 4: two rows, nothing to tell them apart

    dated = [reading(candidates=[patient, doctor]), reading(segment="s2", day=date(2024, 4, 20))]
    match, constraints, _ = resolve_cluster(plan, dated, clinic.cursor())
    assert match.anchor.key == "clinic.visits:visit_id=4" and match.anchor.class_name == "Visit"
    assert match.anchor.links == {
        "Patient": ("clinic.patients:patient_id=10", "AttendedBy"),
        "Clinician": ("clinic.doctors:doctor_id=7", "SeenBy"),
    }
    assert match.related == [] and match.colocated == {}
    assert match.values["visit_code"] == "V-2024-0004" and match.anchor.date == "2024-04-20"
    assert constraints.segments == {"s1"}

    code = LookupValue("visit_code", "equals", "V-2024-0002")
    match, _, queried = resolve_cluster(plan, [reading(lookups=[code])], clinic.cursor())
    assert queried and match.anchor.key == "clinic.visits:visit_id=2"  # an exact code is enough alone


def test_lookups_are_fed_by_the_columns_a_rule_names():
    assert [l.match for l in lookups_for(CONFIG, ["visit_code"], "equals")] == ["equals"]
    assert lookups_for(CONFIG, ["visit_code"], "ends_with") == []  # only fed by visit_code_tail
    assert len(lookups_for(CONFIG, ["visit_code_tail"], "ends_with")) == 1
    assert lookups_for(CONFIG, ["patient_id"], "equals") == []
    # The retail mapping: a ticket number looked up in either table feeds the return's lookup.
    [fed] = lookups_for(RETAIL, ["ss_ticket_number"], "equals")
    assert (fed.column, fed.identifies) == ("sr_ticket_number", "Sale")


def test_too_many_rows_settle_nothing_and_bad_names_are_refused(clinic):
    [plan] = plans(CONFIG)
    rows = clinic.execute("SELECT visit_id, patient_id, doctor_id, visit_date, visit_code FROM clinic.visits").fetchall()
    assert decide_rows(plan, rows, {date(2024, 3, 1)}) is None  # four rows, limit three
    assert decide_rows(plan, rows[:1], set()).anchor.key == "clinic.visits:visit_id=1"
    assert row_match(plan, rows[2]).values == {"visit_id": "3", "patient_id": "11", "doctor_id": "7", "visit_code": "V-2024-0003"}

    unsafe = {**CLINIC, "anchors": [{**CLINIC["anchors"][0], "lookups": [{"column": "visit_code; DROP TABLE x"}]}]}
    with pytest.raises(ValueError, match="not a plain SQL identifier"):
        plans(resolution_config(SourceMapping.model_validate(unsafe)))
    unmapped = {**CLINIC, "anchors": [{**CLINIC["anchors"][0], "class": "Admission"}]}
    with pytest.raises(ValueError, match="class Admission is not mapped"):
        plans(resolution_config(SourceMapping.model_validate(unmapped)))


def test_a_mapping_without_anchors_asks_the_warehouse_nothing():
    plain = resolution_config(SourceMapping.model_validate({**CLINIC, "anchors": []}))
    assert plans(plain) == [] and lookups_for(plain, ["visit_code"], "equals") == []
    assert gather_constraints([]).lookups == {}
