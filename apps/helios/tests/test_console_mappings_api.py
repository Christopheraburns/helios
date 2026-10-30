"""Source mappings as versioned documents with an API (CG-6)."""

import copy
from pathlib import Path

import pytest
import yaml
from helios_core.index import mappings as stored
from helios_core.index import ontology_versions
from helios_core.index.store import duckdb_index_store
from helios_core.ontology.graph import GraphNode, OntologyGraph
from helios_core.ontology.mapping import (
    SourceMapping,
    load_mapping,
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


@pytest.fixture
def index():
    store = duckdb_index_store()
    store.ensure_tables()
    return store


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



@pytest.fixture
def client(index, monkeypatch):
    from apps.helios.console import ontology
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setitem(ontology._index, "store", index)
    monkeypatch.setattr(ontology, "require_ontology_edit", lambda request, org: ALICE)
    graph = OntologyGraph(
        version="0.2.0", nodes=tuple(GraphNode("Class", name) for name in sorted(CLASSES)), edges=()
    )
    ontology_versions.publish(index, graph, "x.yaml", "alice")
    ontology_versions.activate(index, "0.2.0", "alice")
    app = FastAPI()
    app.include_router(ontology.ontology_router)
    return TestClient(app)


def test_import_save_activate_through_the_api(client):
    url = "/api/v1/ontology/mappings"
    assert client.get(url).json() == {"active": {}, "versions": []}
    [shipped] = client.get(f"{url}/shipped").json()
    assert shipped == DOCUMENT

    saved = client.post(url, json={"mapping": shipped, "note": "imported from the shipped example"})
    assert saved.status_code == 201
    assert saved.json() | {"created_at": "", "content_hash": ""} == {
        "version": 1,
        "model": SHIPPED.model,
        "ontology_version": SHIPPED.ontology_version,
        "content_hash": "",
        "created_at": "",
        "created_by": ALICE,
        "note": "imported from the shipped example",
        "is_active": False,
        "created": True,
        "not_checked": [],
    }
    assert client.post(url, json={"mapping": shipped}).json()["created"] is False

    activated = client.post(f"{url}/1:activate")
    assert activated.status_code == 200 and activated.json()["model"] == SHIPPED.model
    listing = client.get(url).json()
    assert listing["active"] == {SHIPPED.model: 1} and listing["versions"][0]["is_active"] is True
    assert client.get(f"{url}/1").json()["mapping"] == DOCUMENT
    assert client.get(f"{url}/7").status_code == 404
    assert client.post(f"{url}/7:activate").status_code == 404


def test_an_invalid_mapping_is_refused_with_its_problems(client, index):
    url = "/api/v1/ontology/mappings"
    broken = changed(entities__0__identifiers__secondary=["c_loyalty_id"])
    checked = client.post(f"{url}:validate", json={"mapping": broken}).json()
    assert checked["valid"] is False and "c_loyalty_id" in checked["problems"][0]
    refused = client.post(url, json={"mapping": broken})
    assert refused.status_code == 422
    assert refused.json()["detail"]["problems"] == checked["problems"]
    assert stored.versions(index) == []

    malformed = client.post(url, json={"mapping": {"model": "x", "surprise": 1}})
    assert malformed.status_code == 422
    assert any("ontology_version" in p for p in malformed.json()["detail"]["problems"])

    unknown_model = client.post(f"{url}:validate", json={"mapping": changed(model="other.ossie.yaml")}).json()
    assert unknown_model["valid"] is True  # nothing to check tables against, and it says so
    assert "no published semantic model named 'other.ossie.yaml'" in unknown_model["not_checked"][0]


def test_activation_rechecks_against_the_current_ontology(client, index):
    stored.save(index, SourceMapping.model_validate(changed(entities__0__class="Shopper")), ALICE)
    refused = client.post("/api/v1/ontology/mappings/1:activate")
    assert refused.status_code == 422
    assert "class Shopper is not in the ontology" in refused.json()["detail"]["problems"][0]
    assert stored.active(index) == {}


def test_the_crawler_reads_the_active_stored_mapping_not_the_file(index, capsys, monkeypatch):
    from apps.helios.crawler import __main__ as crawler_main

    assert crawler_main.resolution_for("0.2.0", index) == (None, None)
    assert "no mapping is active" in capsys.readouterr().out

    stored.save(index, SHIPPED, ALICE)
    stricter = SourceMapping.model_validate(changed(resolution__thresholds__alias_min_score=0.97))
    stored.save(index, stricter, ALICE)
    stored.activate(index, 2, ALICE)
    monkeypatch.setattr(  # reading the repository file at crawl time would be a regression
        "helios_core.ontology.mapping.load_mapping", lambda path: pytest.fail("read the mapping file")
    )
    config, used = crawler_main.resolution_for("0.2.0", index)
    # What the run records about the mapping it resolved against.
    assert (used["model"], used["version"], used["database"]) == ("tpcds.ossie.yaml", 2, "tpcds")
    assert used["content_hash"] == stored.get(index, 2).content_hash
    assert config.thresholds.alias_min_score == 0.97
    assert config == resolution_config(stricter)
    assert "mapping: tpcds.ossie.yaml version 2" in capsys.readouterr().out
    assert not hasattr(crawler_main, "MAPPINGS")


def test_the_editor_can_list_a_models_tables_and_columns(client):
    listed = client.get("/api/v1/ontology/mappings/models").json()
    assert "tpcds.ossie.yaml" in listed["models"] and "Customer" in listed["classes"]

    model = client.get("/api/v1/ontology/mappings/models/tpcds.ossie.yaml").json()
    assert model["database"] == "tpcds"
    customer = next(t for t in model["tables"] if t["name"] == "customer")
    key = next(c for c in customer["columns"] if c["name"] == "c_customer_sk")
    assert key["role"] == "identifier" and key["datatype"] == "Integer" and key["label"]
    assert any(r["from"] == "store_returns" and r["to"] == "customer" for r in model["relationships"])

    assert client.get("/api/v1/ontology/mappings/models/nothing.yaml").status_code == 404


@pytest.fixture
def clinic_client(client, monkeypatch):
    """The API with the clinic's warehouse behind it (second_domain.py)."""
    import second_domain as clinic
    from apps.helios.console import ontology

    warehouse = clinic.build_warehouse()
    warehouse.execute("INSERT INTO clinic.clinicians VALUES (7, 'C-07b', 'Amara Lee-Hart')")  # a repeated key
    asked = []
    monkeypatch.setattr(ontology, "_active_classes", lambda: {*clinic.CLASSES, "AttendedBy", "SeenBy"})
    monkeypatch.setattr(ontology, "warehouse_connection", lambda principal: asked.append(principal) or warehouse.cursor())
    monkeypatch.setattr(ontology, "_actor", lambda request: ALICE)
    client.asked = asked
    client.mapping = clinic.MAPPING.model_dump(mode="json", by_alias=True)
    return client


def test_probing_a_class_reports_its_key_and_shows_its_identifiers(clinic_client):
    probe = lambda name: clinic_client.post(  # noqa: E731
        "/api/v1/ontology/mappings:probe", json={"mapping": clinic_client.mapping, "entity": name}
    )
    patients = probe("Patient").json()
    assert patients["table"] == "clinic.patients" and patients["rows"] == 3 and patients["key_is_unique"]
    assert patients["columns"] == ["patient_id", "mrn", "email", "first_name", "last_name"]
    assert patients["sample"][0] == [10, "MRN-000010", "dana.kim@example.org", "Dana", "Kim"]
    assert patients["findings"] == []

    clinicians = probe("Clinician").json()
    assert (clinicians["rows"], clinicians["distinct_keys"], clinicians["key_is_unique"]) == (3, 2, False)
    assert "not unique" in clinicians["findings"][0]

    assert probe("Pharmacy").status_code == 404
    assert clinic_client.asked and all(p.endswith("alice") for p in clinic_client.asked)  # as the user


def test_probing_an_anchor_runs_the_query_a_crawl_would(clinic_client):
    body = clinic_client.post(
        "/api/v1/ontology/mappings:probe", json={"mapping": clinic_client.mapping, "anchor": "Visit"}
    ).json()
    assert body["kind"] == "anchor" and len(body["sample"]) == 3 and body["findings"] == []
    assert "FROM clinic.visits" in body["sql"][0]


def test_a_probe_reports_what_the_warehouse_says_and_never_runs_an_invalid_mapping(clinic_client):
    wrong_column = copy.deepcopy(clinic_client.mapping)
    wrong_column["entities"][0]["identifiers"]["secondary"] = ["nhs_number"]
    refused = clinic_client.post("/api/v1/ontology/mappings:probe", json={"mapping": wrong_column, "entity": "Patient"})
    assert refused.status_code == 422
    assert refused.json()["detail"]["message"] == "the warehouse refused the query"
    assert "nhs_number" in refused.json()["detail"]["problems"][0]

    asked = len(clinic_client.asked)
    injected = copy.deepcopy(clinic_client.mapping)
    injected["entities"][0]["identifiers"]["primary"] = ["patient_id; DROP TABLE clinic.patients"]
    refused = clinic_client.post("/api/v1/ontology/mappings:probe", json={"mapping": injected, "entity": "Patient"})
    assert refused.status_code == 422 and refused.json()["detail"]["message"] == "invalid mapping"
    assert len(clinic_client.asked) == asked  # the warehouse was not reached

    both = clinic_client.post("/api/v1/ontology/mappings:probe", json={"mapping": clinic_client.mapping})
    assert both.status_code == 400
