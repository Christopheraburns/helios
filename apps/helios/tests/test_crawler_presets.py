"""Settings schema 2 (CG-1): the engine has no rules; rules come from presets."""

import json
from pathlib import Path

import pytest
from apps.helios.crawler.crawl import crawl
from crawler_samples import RETAIL_SETTINGS
from helios_core.crawler.settings import (
    EMPTY_SETTINGS,
    SCHEMA_VERSION,
    CrawlerSettings,
    load_preset,
    preset_document,
    presets,
)
from helios_core.index import crawler_settings as versions
from helios_core.index.store import duckdb_index_store
from test_crawler_llm import _connector
from test_crawler_resolution import CONFIG, gazetteer, warehouse  # noqa: F401 - fixtures

# The rules that were built into the crawler before schema 2, as that code dumped them.
SCHEMA_1 = json.loads((Path(__file__).parent / "baselines" / "settings-schema-1.json").read_text())


def test_the_engine_default_is_an_empty_document():
    assert SCHEMA_VERSION == "2" and EMPTY_SETTINGS == CrawlerSettings()
    assert EMPTY_SETTINGS.is_empty()
    assert (EMPTY_SETTINGS.patterns, EMPTY_SETTINGS.pdf_labels, EMPTY_SETTINGS.contextual) == ([], [], {})
    assert EMPTY_SETTINGS.cases.identifiers == [] and EMPTY_SETTINGS.claims.cues == {}
    assert "retail" not in EMPTY_SETTINGS.llm.instructions.lower()
    assert not RETAIL_SETTINGS.is_empty()


def test_the_preset_is_exactly_the_rules_that_used_to_be_built_in():
    assert presets()["retail-returns"]["title"] == "Retail returns"
    assert RETAIL_SETTINGS == load_preset("retail-returns")
    # Every section schema 1 had is unchanged; what was in code then is now beside it.
    now = RETAIL_SETTINGS.model_dump(mode="json")
    for section, before in SCHEMA_1.items():
        if section == "schema_version":
            continue
        if section == "patterns":
            assert [{k: v for k, v in p.items() if k != "key_name"} for p in now["patterns"]] == before
        elif section == "cases":
            assert {k: v for k, v in now["cases"].items() if k != "max_hub_documents"} == before
        elif section == "claims":  # the cue words; the kinds of claim were in code
            assert {k: now["claims"][k] for k in before} == before
        else:
            assert now[section] == before, section
    moved_from_code = set(now) - set(SCHEMA_1)
    assert moved_from_code == {
        "class_cues", "cue_window", "header_rules", "dictionary", "date_formats", "about", "resolution",
    }
    assert preset_document("retail-returns")["schema_version"] == "2"
    with pytest.raises(KeyError):
        load_preset("no-such-preset")


def test_a_stored_schema_1_document_is_read_as_those_rules_plus_its_own_sections():
    assert SCHEMA_1["schema_version"] == "1"
    assert CrawlerSettings.model_validate(SCHEMA_1) == RETAIL_SETTINGS
    # Saved before the llm section existed: it still gets the instructions it ran with.
    older = {k: v for k, v in SCHEMA_1.items() if k != "llm"}
    assert CrawlerSettings.model_validate(older).llm == RETAIL_SETTINGS.llm
    # Its own edits win.
    edited = json.loads(json.dumps(SCHEMA_1))
    edited["analyzers"]["chat"]["enabled"] = False
    edited["pdf_labels"] = edited["pdf_labels"][:2]
    read = CrawlerSettings.model_validate(edited)
    assert not read.analyzers.chat.enabled and len(read.pdf_labels) == 2
    assert read.claims == RETAIL_SETTINGS.claims


def test_a_schema_1_version_in_the_index_still_loads():
    index = duckdb_index_store()
    index.ensure_tables()
    from helios_core.index.records import CrawlerSettingsRecord

    index.append(
        versions.VERSIONS,
        [
            CrawlerSettingsRecord(
                version=1,
                content_hash="recorded-under-schema-1",
                settings_json=json.dumps(SCHEMA_1, sort_keys=True),
                created_at="2026-10-01T00:00:00Z",
                created_by="alice",
            )
        ],
    )
    versions.activate(index, 1, "alice")
    record, settings = versions.active(index)
    assert record.version == 1 and settings == RETAIL_SETTINGS


def _crawl(tmp_path, settings, gazetteer=None, warehouse=None):  # noqa: F811
    index = duckdb_index_store()
    index.ensure_tables()
    return crawl(
        index,
        _connector(tmp_path),
        "ds-1",
        actor="test",
        ontology_version="0.2.0",
        settings=None,
        settings_hash=settings.content_hash(),
        crawler_settings=settings,
        gazetteer=gazetteer,
        resolution=CONFIG if gazetteer is not None else None,
        warehouse_cursor=warehouse.cursor if warehouse is not None else None,
    )


def test_an_empty_configuration_crawls_and_finds_only_segments(tmp_path):
    run = _crawl(tmp_path, EMPTY_SETTINGS)
    assert run.status == "SUCCEEDED"
    assert run.counts == {"listed": 7, "analyzed": 7, "segments": 13, "mentions": 0}


def test_empty_settings_with_a_mapping_find_only_what_the_dictionary_names(
    tmp_path, warehouse, gazetteer  # noqa: F811
):
    empty = _crawl(tmp_path / "a", EMPTY_SETTINGS, gazetteer, warehouse)
    full = _crawl(tmp_path / "b", RETAIL_SETTINGS, gazetteer, warehouse)
    assert empty.status == "SUCCEEDED" and empty.counts["segments"] == 13
    assert 0 < empty.counts["mentions"] < full.counts["mentions"]  # names, no patterns or labels
    assert empty.counts["claims"] == 0 and full.counts["claims"] > 0
    # Nothing links documents into cases without identifier patterns: one case per document.
    assert empty.counts["cases"] == 7 and full.counts["cases"] == 4


@pytest.fixture
def client(monkeypatch):
    from apps.helios.console import crawler, ontology
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    index = duckdb_index_store()
    index.ensure_tables()
    monkeypatch.setitem(ontology._index, "store", index)
    monkeypatch.setattr(ontology, "_actor", lambda request: "cloudera-workbench:alice")
    app = FastAPI()
    app.include_router(crawler.crawler_router)
    return TestClient(app)


def test_the_api_offers_presets_and_says_when_nothing_is_configured(client):
    current = client.get("/api/v1/crawler/settings").json()
    assert current["using_defaults"] and current["empty"] is True
    assert current["settings"]["patterns"] == []
    assert client.get("/api/v1/crawler/settings/defaults").json()["settings"] == current["settings"]

    [listed] = client.get("/api/v1/crawler/settings/presets").json()
    assert (listed["name"], listed["title"]) == ("retail-returns", "Retail returns")
    preset = client.get("/api/v1/crawler/settings/presets/retail-returns").json()
    assert preset["settings"] == RETAIL_SETTINGS.model_dump(mode="json")
    assert preset["content_hash"] == RETAIL_SETTINGS.content_hash()
    assert client.get("/api/v1/crawler/settings/presets/unknown").status_code == 404

    saved = client.post("/api/v1/crawler/settings", json={"settings": preset["settings"], "note": "preset"})
    assert saved.status_code in (200, 201)
    version = saved.json()["version"]
    assert client.post(f"/api/v1/crawler/settings/{version}:activate").status_code == 200
    active = client.get("/api/v1/crawler/settings").json()
    assert active["empty"] is False and active["active_version"] == version


# --- CG-2 and CG-3: mention and identity rules are settings, not code ---------------


def _edited(**sections):
    document = RETAIL_SETTINGS.model_dump(mode="json")
    document.update(sections)
    return CrawlerSettings.model_validate(document)


def test_class_cues_come_from_the_settings():
    from apps.helios.crawler.mentions import cue_distance, far

    text = "She went to the Midway store on Monday."
    start = text.index("Midway")
    end = start + len("Midway")
    assert cue_distance(text, start, end, "Store", RETAIL_SETTINGS) == 1  # "store" follows
    assert cue_distance(text, start, end, "Reason", RETAIL_SETTINGS) == 0  # a class with no cues
    assert cue_distance(text, start, end, "Store", EMPTY_SETTINGS) == 0  # no cues at all

    branch = _edited(class_cues={"Store": [r"\bbranch\b"]}, cue_window=10)
    assert cue_distance(text, start, end, "Store", branch) == far(branch) == 11
    assert cue_distance("the Midway branch", 4, 10, "Store", branch) == 1

    with pytest.raises(ValueError, match="class_cues.Store"):
        _edited(class_cues={"Store": ["(unclosed"]})


def test_header_rules_decide_who_a_header_names(tmp_path, warehouse, gazetteer):  # noqa: F811
    def header_mentions(settings):
        index = duckdb_index_store()
        index.ensure_tables()
        run = crawl(
            index, _connector(tmp_path / str(id(settings))), "ds-1", actor="test",
            ontology_version="0.2.0", settings=None, settings_hash=settings.content_hash(),
            crawler_settings=settings, gazetteer=gazetteer, resolution=CONFIG,
            warehouse_cursor=warehouse.cursor,
        )
        mentions = index.read("helios_index.mentions", {"crawl_run_id": run.crawl_run_id})
        return {(m.surface_form, m.proposed_class) for m in mentions if m.extractor_detail.startswith("header:")}

    assert ("Wilma Graham", "Customer") in header_mentions(RETAIL_SETTINGS)
    assert header_mentions(_edited(header_rules=[])) == set()


def test_ordinary_words_and_thresholds_shape_the_dictionary(warehouse):  # noqa: F811
    from apps.helios.crawler.gazetteer import Gazetteer
    from test_crawler_resolution import CLASSES

    from helios_core.crawler.settings import Dictionary

    tuned = Gazetteer.build(warehouse.cursor, CONFIG, CLASSES, RETAIL_SETTINGS.dictionary)
    assert tuned.forms["Store", "ese"].low_specificity  # three letters: too short to be a name alone
    lenient = Gazetteer.build(warehouse.cursor, CONFIG, CLASSES, Dictionary(short_token=2))
    assert not lenient.forms["Store", "ese"].low_specificity
    assert set(tuned.forms) == set(lenient.forms)  # the same names; only how they are judged differs
    listed = Gazetteer.build(
        warehouse.cursor, CONFIG, CLASSES, Dictionary(short_token=2, ordinary_words=["ese"])
    )
    assert listed.forms["Store", "ese"].low_specificity  # ... unless the settings call it ordinary
    assert next(iter(tuned.forms.values())).instances[0].startswith("tpcds.")  # the mapping's database


def test_document_key_names_dates_and_about_come_from_the_settings():
    from apps.helios.crawler.cases import Rules, parse_date
    from apps.helios.crawler.resolution import Link, about_target
    from helios_core.index.records import MentionRecord

    rules = Rules(RETAIL_SETTINGS)
    assert rules.key_name("return_authorization") == "rma"
    assert rules.key_name("ticket_number") == "ticket_number"  # default: the pattern's own name

    assert parse_date("June 14, 2001", rules.date_formats).isoformat() == "2001-06-14"
    assert parse_date("June 14, 2001") is None  # the engine knows only ISO dates
    assert parse_date("2001-06-14T09:00:00Z").isoformat() == "2001-06-14"
    assert parse_date("14/06/2001", ["%d/%m/%Y"]).isoformat() == "2001-06-14"

    def link(target, class_name, segment):
        mention = MentionRecord(
            crawl_run_id="r", mention_id=target, asset_id="a", segment_id=segment,
            surface_form=target, locator={}, extractor="pattern",
        )
        return Link(mention, target, class_name, "SameAs", "exact_key", 1.0, [segment])

    links = [link("store-1", "Store", "body"), link("item-1", "Item", "body")]
    segments = {"body": "email_body", "subject": "email_subject"}
    assert about_target(links, segments, None, RETAIL_SETTINGS.about) == "item-1"  # Item before Store
    stores_first = RETAIL_SETTINGS.about.model_copy(update={"class_priority": ["Store", "Item"]})
    assert about_target(links, segments, None, stores_first) == "store-1"
    titled = [*links, link("store-9", "Store", "subject")]
    assert about_target(titled, segments, None, RETAIL_SETTINGS.about) == "store-9"  # named in the title
    no_titles = RETAIL_SETTINGS.about.model_copy(update={"title_segments": []})
    assert about_target(titled, segments, None, no_titles) == "item-1"
    assert about_target(links, segments, "the-case", RETAIL_SETTINGS.about) == "the-case"


def test_the_database_comes_from_the_mapping():
    from helios_core.ontology.mapping import SourceMapping, load_mapping, mapping_problems, resolution_config
    from test_crawler_resolution import REPO_ROOT

    shipped = load_mapping(REPO_ROOT / "ontology/mappings/ossie/tpcds.yaml")
    assert shipped.database == "tpcds" and resolution_config(shipped).database == "tpcds"
    document = shipped.model_dump(mode="json", by_alias=True)
    unnamed = SourceMapping.model_validate({**document, "database": "", "model": "sales.ossie.yaml"})
    assert resolution_config(unnamed).database == "sales"  # from the model's name
    named = SourceMapping.model_validate({**document, "database": "warehouse_eu"})
    assert resolution_config(named).database == "warehouse_eu"
    bad = SourceMapping.model_validate({**document, "database": "a; drop"})
    assert any("not a plain database name" in p for p in mapping_problems(bad))


# --- CG-5: claims are settings, not code ----------------------------------------------


def test_a_schema_1_claims_section_keeps_its_cue_words_and_gains_the_claim_definitions():
    edited = json.loads(json.dumps(SCHEMA_1))
    edited["claims"]["cues"]["REFUND_APPROVED"] = ["refund signed off"]
    read = CrawlerSettings.model_validate(edited)
    assert read.claims.cues["REFUND_APPROVED"] == ["refund signed off"]  # its own words win
    assert read.claims.predicates == RETAIL_SETTINGS.claims.predicates  # what the code used to know
    assert read.claims.weak_words == RETAIL_SETTINGS.claims.weak_words
    assert read.claims.speakers == RETAIL_SETTINGS.claims.speakers


def _claims(tmp_path, warehouse, gazetteer, settings):  # noqa: F811
    index = duckdb_index_store()
    index.ensure_tables()
    run = crawl(
        index, _connector(tmp_path / str(id(settings))), "ds-1", actor="test",
        ontology_version="0.2.0", settings=None, settings_hash=settings.content_hash(),
        crawler_settings=settings, gazetteer=gazetteer, resolution=CONFIG,
        warehouse_cursor=warehouse.cursor,
    )
    found = index.read("helios_index.claims", {"crawl_run_id": run.crawl_run_id})
    return sorted(c.predicate for c in found), run.counts


def _with_claims(**changes):
    document = RETAIL_SETTINGS.model_dump(mode="json", by_alias=True)
    document["claims"].update(changes)
    return CrawlerSettings.model_validate(document)


def test_which_claims_exist_and_who_may_make_them_come_from_the_settings(
    tmp_path, warehouse, gazetteer  # noqa: F811
):
    baseline, counts = _claims(tmp_path, warehouse, gazetteer, RETAIL_SETTINGS)
    assert set(baseline) == set(RETAIL_SETTINGS.claims.predicates) and counts["claims"] == len(baseline)

    # Cue words alone state nothing: without a definition a kind of claim does not exist.
    definitions = RETAIL_SETTINGS.model_dump(mode="json", by_alias=True)["claims"]["predicates"]
    only_damage = {"PACKAGING_DAMAGED": definitions["PACKAGING_DAMAGED"]}
    found, _ = _claims(tmp_path, warehouse, gazetteer, _with_claims(predicates=only_damage))
    assert set(found) == {"PACKAGING_DAMAGED"}
    assert _claims(tmp_path, warehouse, gazetteer, _with_claims(predicates={}))[0] == []

    # Where a role looks is a setting. Told to look only at the case's own entity, a claim
    # is found while the settings say what a case is about, and not once they stop saying so.
    case_only = json.loads(json.dumps(definitions))
    for definition in case_only.values():
        for role in (definition["subject"], definition["object"]):
            if role["class"] == "Return":
                role["find"] = ["case"]
    found, _ = _claims(tmp_path, warehouse, gazetteer, _with_claims(predicates=case_only))
    assert found  # the case's entity is known
    found, counts = _claims(
        tmp_path, warehouse, gazetteer, _with_claims(predicates=case_only, case_classes=[])
    )
    assert found == [] and counts["claims_unanchored"] > 0

    # Refusing every speaker a kind of claim silences it.
    silenced = json.loads(json.dumps(definitions))
    silenced["REFUND_APPROVED"]["blocked_speakers"] = ["customer", "staff", "unknown"]
    found, _ = _claims(tmp_path, warehouse, gazetteer, _with_claims(predicates=silenced))
    assert "REFUND_APPROVED" not in found and "REFUND_REQUESTED" in found


def test_speaker_rules_and_role_finding_are_generic():
    from types import SimpleNamespace

    from apps.helios.crawler.claims import Context
    from helios_core.crawler.settings import Claims

    rules = Claims.model_validate(
        {
            "case_classes": ["Visit"],
            "speakers": [
                {"segment": "message", "field": "role", "values": ["patient"], "speaker": "patient"},
                {"segment": "message", "field": "role", "speaker": "clinician"},
                {"segment": "page", "speaker": "clinic"},
            ],
        }
    )
    context = Context({}, [], [], [], [], [], rules)

    def unit(segment_type, **structure):
        return SimpleNamespace(segment=SimpleNamespace(segment_type=segment_type, structure=structure, asset_id="a"))

    assert context.voice(unit("message", role="Patient")) == "patient"
    assert context.voice(unit("message", role="nurse")) == "clinician"
    assert context.voice(unit("message")) == "unknown"  # no role recorded
    assert context.voice(unit("page")) == "clinic"
    assert context.voice(unit("email_body")) == "unknown"  # no rule for emails here
    assert Context({}, [], [], [], [], []).voice(unit("page")) == "unknown"  # no rules at all

    with pytest.raises(ValueError):
        Claims.model_validate({"predicates": {"X": {"subject": {"class": "A", "find": ["anywhere"]}, "object": {"class": "B", "find": ["in_unit"]}}}})
    with pytest.raises(ValueError):
        Claims.model_validate({"predicates": {"X": {"subject": {"class": "A", "find": []}, "object": {"class": "B", "find": ["in_unit"]}}}})


def test_text_rules_come_from_the_settings():
    from apps.helios.crawler.claims import blocked, compile_cue, cue_strength, sentence_spans

    text = "Seen by Dr. Lee. Approx. two weeks."
    assert [text[s:e] for s, e in sentence_spans(text)] == ["Seen by Dr.", "Lee.", "Approx.", "two weeks."]
    assert [text[s:e] for s, e in sentence_spans(text, ["dr", "approx"])] == ["Seen by Dr. Lee.", "Approx. two weeks."]
    assert cue_strength("approved") == "medium" and cue_strength("approved", ["approved"]) == "weak"
    assert compile_cue("refund * approved", 1).search("refund was approved")
    assert not compile_cue("refund * approved", 1).search("refund was finally approved")
    sentence = "it was not at all really approved"
    at = sentence.index("approved")
    assert not blocked(sentence, at, ["not"], [], window=3)  # "not" is four words back
    assert blocked(sentence, at, ["not"], [], window=4)
