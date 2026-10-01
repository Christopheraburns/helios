"""CR-0a: the TPC-DS ontology mapping agrees with the published Ossie model and the ontology."""

from pathlib import Path

import pytest
import yaml
from helios_core.ontology.mapping import load_mappings, resolution_config, template_columns
from helios_core.ontology.parser import parse
from linkml_runtime.utils.schemaview import SchemaView

REPO_ROOT = Path(__file__).resolve().parents[3]
MAPPING = REPO_ROOT / "ontology/mappings/ossie/tpcds.yaml"
OSSIE = REPO_ROOT / "models/published/tpcds.ossie.yaml"
RETAIL = REPO_ROOT / "ontology/packs/retail/retail.yaml"


@pytest.fixture(scope="module")
def mapping():
    return yaml.safe_load(MAPPING.read_text())


@pytest.fixture(scope="module")
def ossie():
    model = yaml.safe_load(OSSIE.read_text())
    return {
        "datasets": {
            d["name"]: {f["name"] for f in d.get("fields", [])} for d in model["datasets"]
        },
        "relationships": {r["name"] for r in model["relationships"]},
        "metrics": {m["name"] for m in model["metrics"]},
    }


@pytest.fixture(scope="module")
def view():
    return SchemaView(str(RETAIL))


def test_every_entity_maps_an_existing_dataset_and_columns(mapping, ossie):
    for entity in mapping["entities"]:
        fields = ossie["datasets"].get(entity["ossie_element"])
        assert fields is not None, entity["ossie_element"]
        ids = entity["identifiers"]
        columns = [
            c
            for part in ("primary", "secondary", "display", "aliases")
            for c in ids.get(part) or []
        ]
        columns += [c for a in entity.get("attributes", []) for c in a["columns"]]
        columns += [c for t in ids.get("alias_templates") or [] for c in template_columns(t)]
        assert ids["primary"], entity["class"]
        missing = [c for c in columns if c not in fields]
        assert not missing, (entity["class"], missing)


def test_every_mapped_class_and_attribute_exists_in_the_ontology(mapping, view):
    classes = set(view.all_classes())
    for entity in mapping["entities"]:
        assert entity["class"] in classes, entity["class"]
        slots = {s.name for s in view.class_induced_slots(entity["class"])}
        for attribute in entity.get("attributes", []):
            assert attribute["attribute"] in slots, (entity["class"], attribute["attribute"])


def test_relationships_exist_and_use_relationship_types(mapping, ossie, view):
    for rel in mapping["relationships"]:
        assert rel["ossie_relationship"] in ossie["relationships"], rel["ossie_relationship"]
        assert "Relationship" in view.class_ancestors(rel["edge"]), rel["edge"]


def test_concepts_point_at_existing_metrics(mapping, ossie):
    for concept in mapping["concepts"]:
        kind, _, name = concept["ossie_element"].partition(".")
        assert kind == "metrics" and name in ossie["metrics"], concept


def test_resolution_scope_covers_the_helios_ds_corpus_and_known_classes(mapping, view):
    scopes = mapping["resolution"]["scope"]
    assert any("helios-db/source/datasets/" in s["source_pattern"] for s in scopes)
    classes = set(view.all_classes())
    for scope in scopes:
        assert set(scope["candidate_classes"]) <= classes


def test_ground_truth_entity_types_all_have_a_class(mapping):
    # The Helios-DS ground truth types the crawler is scored against.
    mapped = {e["class"] for e in mapping["entities"]}
    assert {"Customer", "Item", "Store", "Sale", "Return", "Reason", "Brand"} <= mapped


def test_retail_claim_vocabulary_is_declared(view):
    predicates = set(view.get_enum("RetailClaimPredicate").permissible_values)
    assert predicates == {
        "PACKAGING_DAMAGED",
        "RETURN_REASON",
        "REFUND_REQUESTED",
        "REFUND_APPROVED",
    }


def test_mapping_targets_the_current_pack_version(mapping, view):
    assert mapping["ontology_version"] == f"helios_retail@{view.schema.version}"


# --- CR-0b: typed mappings, the resolver's config, and mappings in the graph ---------

EXTENSION = REPO_ROOT / "ontology/customers/example-tenant/extension.yaml"
CORE = REPO_ROOT / "ontology/core/core.yaml"
CORPUS = "s3a://applied-ai-buk-d5eff1ab/helios-db/source/datasets/abc/artifacts/x.pdf"


def test_resolution_config_gives_the_resolver_identifiers_per_class():
    [mapping] = load_mappings(REPO_ROOT / "ontology/mappings/ossie")
    config = resolution_config(mapping)
    for name in ("Customer", "Store", "Item", "Sale", "Return", "Reason", "Brand"):
        assert config.classes[name].primary, name
    assert config.classes["Customer"].secondary == ["c_customer_id", "c_email_address"]
    assert config.classes["Brand"].ossie_element == "item"
    assert config.thresholds.alias_min_score == 0.92
    assert "Reason" in config.candidate_classes(CORPUS)


def test_published_graph_carries_the_mapping_and_nothing_is_broken():
    mappings = load_mappings(REPO_ROOT / "ontology/mappings/ossie")
    model = yaml.safe_load(OSSIE.read_text())
    result = parse(str(EXTENSION), mappings=mappings, ossie_model=model)
    assert result.broken_mappings == []
    maps_to = {
        (e.from_key, e.to_key): e.properties
        for e in result.graph.edges
        if e.type == "MAPS_TO" and e.from_label == "OssieElement" and e.to_label == "Class"
    }
    assert maps_to[("item", "Brand")]["primary"] == ["i_brand_id"]
    assert ("store_returns", "Return") in maps_to
    materialises = {
        (e.from_key, e.to_key)
        for e in result.graph.edges
        if e.type == "MATERIALISES_AS" and e.from_label == "OssieElement"
    }
    assert ("store_returns__sr_reason_sk__reason", "HasReason") in materialises


def test_broken_mappings_are_reported():
    mappings = load_mappings(REPO_ROOT / "ontology/mappings/ossie")
    model = yaml.safe_load(OSSIE.read_text())
    model["datasets"] = [d for d in model["datasets"] if d["name"] != "reason"]
    result = parse(str(EXTENSION), mappings=mappings, ossie_model=model)
    assert {"class": "", "mapped_to": "reason", "status": "not_found"} in result.broken_mappings
    statuses = {
        n.key: n.properties["status"] for n in result.graph.nodes if n.label == "OssieElement"
    }
    assert statuses["reason"] == "not_found" and statuses["item"] == "ok"


def test_a_mapping_only_applies_to_ontologies_that_import_its_pack():
    mappings = load_mappings(REPO_ROOT / "ontology/mappings/ossie")
    result = parse(str(CORE), mappings=mappings)
    assert not [n for n in result.graph.nodes if n.label == "OssieElement"]
    assert result.broken_mappings == []


def test_alias_templates_reach_the_resolver_and_the_graph():
    [mapping] = load_mappings(REPO_ROOT / "ontology/mappings/ossie")
    config = resolution_config(mapping)
    assert "{c_salutation} {c_last_name}" in config.classes["Customer"].alias_templates
    assert config.classes["Store"].alias_templates == ["{s_city} store"]
    assert template_columns("{i_color} {i_class} item") == ["i_color", "i_class"]
    result = parse(str(EXTENSION), mappings=[mapping])
    store_map = next(
        e
        for e in result.graph.edges
        if e.type == "MAPS_TO" and e.from_key == "store" and e.to_key == "Store"
    )
    assert store_map.properties["alias_templates"] == ["{s_city} store"]
