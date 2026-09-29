"""Dataset and manifest-viewer endpoints, against a dataset published by the pipeline."""

import hashlib

import duckdb
import pytest
from fastapi.testclient import TestClient

from helios_ds.config import ArtifactConfig, DatasetConfig, ScenarioConfig
from helios_ds.jobs import JobStore
from helios_ds.lakehouse import DUCKDB, SqlLakehouseSink
from helios_ds.manifests import manifest_key
from helios_ds.object_store import LocalObjectStore
from helios_ds.pipeline import plan_and_publish
from helios_ds_generation.api import main
from helios_ds_generation.api.v1.service import GenerationService, get_service


class NoDispatch:
    name = "inline"

    def dispatch(self, job_id):
        return None

    def stop(self, run_id):
        pass


CONFIG = DatasetConfig(
    artifacts={
        "pdf": ArtifactConfig(target_count=6),
        "email": ArtifactConfig(target_count=4),
        "video": ArtifactConfig(target_count=2),
    },
    scenarios={
        "product_return_damage": ScenarioConfig(weight=0.5),
        "warehouse_inventory_issue": ScenarioConfig(weight=0.5),
    },
)


@pytest.fixture
def published(tmp_path, small_repo, templates):
    sink = SqlLakehouseSink(lambda: duckdb.connect(str(tmp_path / "lakehouse.duckdb")), DUCKDB)
    sink.ensure_tables()
    store = LocalObjectStore(str(tmp_path / "objects"))
    result = plan_and_publish(CONFIG, small_repo, templates, sink, store)
    main.app.dependency_overrides[get_service] = lambda: GenerationService(
        JobStore(sink), NoDispatch()
    )
    yield TestClient(main.app), result, store
    main.app.dependency_overrides.clear()


def test_list_and_get_dataset(published):
    client, result, _ = published
    [summary] = client.get("/v1/datasets").json()
    assert summary["dataset_id"] == result.dataset_id
    assert summary["state"] == "IN_REVIEW"
    assert summary["manifest_sha256"] == result.manifest_sha256
    detail = client.get(f"/v1/datasets/{result.dataset_id}").json()
    assert [e["state"] for e in detail["lifecycle"]] == ["CREATING", "VALIDATING", "IN_REVIEW"]
    assert detail["planned_artifact_count"] == 12


def test_manifest_overview_has_everything_but_scenarios(published):
    client, result, _ = published
    overview = client.get(f"/v1/datasets/{result.dataset_id}/manifest").json()
    assert "scenarios" not in overview
    assert overview["artifact_counts"]["pdf"] == {"target": 6, "planned": 6}
    assert set(overview["scenario_counts"]) == {
        "product_return_damage",
        "warehouse_inventory_issue",
    }
    assert overview["source_fingerprint"]["backend"] == "duckdb"
    assert overview["identity"]["config_hash"] == CONFIG.config_hash()
    assert len(overview["templates"]) == 15


def test_raw_download_is_the_exact_published_bytes(published):
    client, result, _ = published
    response = client.get(f"/v1/datasets/{result.dataset_id}/manifest/raw")
    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == result.manifest_sha256
    assert "attachment" in response.headers["content-disposition"]


def test_scenarios_are_paginated_filterable_and_readable(published):
    client, result, _ = published
    url = f"/v1/datasets/{result.dataset_id}/scenarios"
    everything = client.get(url, params={"limit": 500}).json()
    assert everything["total"] == len(everything["items"]) > 0
    page = client.get(url, params={"limit": 2, "offset": 1}).json()
    assert page["items"] == everything["items"][1:3]

    returns = client.get(url, params={"scenario_type": "product_return_damage"}).json()
    assert returns["total"] > 0
    assert all(i["scenario_type"] == "product_return_damage" for i in returns["items"])
    assert " returned " in returns["items"][0]["headline"]
    assert "?" not in returns["items"][0]["headline"]

    warehouse = [
        i for i in everything["items"] if i["scenario_type"] == "warehouse_inventory_issue"
    ]
    assert "video" in {t for i in warehouse for t in i["artifact_types"]}

    word = returns["items"][0]["headline"].split()[0]
    found = client.get(url, params={"q": word.upper()}).json()
    assert returns["items"][0]["scenario_id"] in [i["scenario_id"] for i in found["items"]]


def test_scenario_detail(published):
    client, result, _ = published
    first = client.get(f"/v1/datasets/{result.dataset_id}/scenarios").json()["items"][0]
    detail = client.get(f"/v1/datasets/{result.dataset_id}/scenarios/{first['scenario_id']}").json()
    assert detail["headline"] == first["headline"]
    assert detail["facts"] and detail["source_refs"] and detail["artifacts"]
    assert {a["artifact_type"] for a in detail["artifacts"]} == set(first["artifact_types"])
    missing = client.get(f"/v1/datasets/{result.dataset_id}/scenarios/nope")
    assert missing.status_code == 404


def test_tampered_manifest_is_reported_not_served(published):
    client, result, store = published
    (store.root / manifest_key(result.dataset_id)).write_bytes(b"{}")
    # A fresh service (empty cache) must detect the mismatch.
    response = client.get(f"/v1/datasets/{result.dataset_id}/manifest")
    assert response.status_code == 502
    assert "SHA-256" in response.json()["detail"]


def test_unknown_dataset_is_404(published):
    client, _, _ = published
    for path in ("", "/manifest", "/manifest/raw", "/scenarios", "/artifacts"):
        assert client.get(f"/v1/datasets/nope{path}").status_code == 404


def test_search_covers_every_fact(published):
    client, result, _ = published
    url = f"/v1/datasets/{result.dataset_id}/scenarios"
    first = client.get(url).json()["items"][0]
    detail = client.get(f"{url}/{first['scenario_id']}").json()
    email = detail["facts"].get("c_email_address") or detail["facts"]["i_item_id"]
    found = client.get(url, params={"q": email}).json()["items"]
    assert first["scenario_id"] in [i["scenario_id"] for i in found]


def test_every_headline_field_comes_from_the_scenario_query(small_repo):
    import string

    from helios_ds.scenarios import SCENARIOS

    for definition in SCENARIOS.values():
        fields = {f for _, f, _, _ in string.Formatter().parse(definition.headline) if f}
        columns = set(small_repo.columns(definition.eligibility_sql))
        assert fields and fields <= columns, (definition.scenario_type, fields - columns)
        for row in small_repo.query(definition.eligibility_sql + " LIMIT 5"):
            assert "{" not in definition.describe(row)


def test_concurrent_viewer_requests(published):
    """The viewer fetches the dataset and manifest at once while jobs are polled."""
    from concurrent.futures import ThreadPoolExecutor

    client, result, _ = published
    base = f"/v1/datasets/{result.dataset_id}"
    paths = [base, f"{base}/manifest", "/v1/jobs", "/v1/datasets", f"{base}/scenarios"] * 4
    with ThreadPoolExecutor(8) as pool:
        codes = list(pool.map(lambda p: client.get(p).status_code, paths))
    assert codes == [200] * len(paths)
