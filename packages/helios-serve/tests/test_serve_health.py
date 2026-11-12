"""``health(stores)`` (burndown 6.4): the registry and the published models, and with
``deep`` each warehouse connection and the catalog; independent checks, fixed
descriptions, and no exception text in the result or the log."""
import json
import logging
from dataclasses import replace

import pytest

from helios_cortex.registry import SqlRegistry
from helios_serve import HealthComponent, aggregate_status, health

SECRET = "https://svc:hunter2@warehouse.internal/token"


def statuses(result):
    return {c["id"]: c["status"] for c in result["components"]}


class Raises:
    def __init__(self, exc=None):
        self.exc = exc or RuntimeError(SECRET)

    def __call__(self, *args, **kwargs):
        raise self.exc

    def ping(self):
        raise self.exc

    def data_source(self, data_source_id):
        raise self.exc

    def sources(self):
        raise self.exc


class Pings:
    def __init__(self, answer):
        self.answer = answer

    def ping(self):
        return self.answer


def test_a_serving_deployment_is_healthy(stores):
    result = health(stores)
    assert result["status"] == "healthy" and result["model_count"] == 1
    assert statuses(result) == {"registry": "healthy", "models": "healthy"}
    assert result["version"]
    assert json.loads(json.dumps(result)) == result

    deep = health(stores, deep=True)          # the DuckDB connection answers; no catalog is configured
    assert statuses(deep) == {"registry": "healthy", "models": "healthy", "connection:default": "healthy",
                              "catalog": "unknown"}
    assert deep["status"] == "healthy"


def test_a_registry_without_its_schema_is_degraded(stores, tmp_path):
    registry = SqlRegistry(f"sqlite:///{tmp_path / 'registry.db'}")          # never upgraded
    result = health(replace(stores, registry=registry))
    assert statuses(result)["registry"] == "degraded" and result["status"] == "degraded"
    assert "upgrade" in result["components"][0]["description"]
    registry.upgrade()
    assert health(replace(stores, registry=registry))["status"] == "healthy"


def test_an_unreadable_registry_or_model_store_is_unavailable_and_says_nothing_of_why(stores, caplog):
    caplog.set_level(logging.WARNING)
    down = health(replace(stores, registry=Raises()))
    assert down["status"] == "unavailable" and statuses(down)["registry"] == "unavailable"
    assert statuses(down)["models"] == "healthy"                            # checked anyway
    no_models = health(replace(stores, models=Raises()))
    assert no_models["status"] == "unavailable" and no_models["model_count"] is None
    assert "hunter2" not in json.dumps([down, no_models]) and "warehouse.internal" not in json.dumps(down)
    assert "hunter2" not in caplog.text and "RuntimeError" in caplog.text


def test_nothing_published_is_degraded(stores, tmp_path):
    from helios_cortex.artifacts import ArtifactStore
    from helios_serve import ModelStore

    result = health(replace(stores, models=ModelStore(ArtifactStore(tmp_path / "empty"))))
    assert (result["status"], result["model_count"], statuses(result)["models"]) == ("degraded", 0, "degraded")


def test_a_deep_check_pings_each_connection_and_the_catalog(stores):
    settings = stores.settings.model_copy(update={"connections": {
        **stores.settings.connections, "lake": stores.settings.connections["default"],
        "broken": stores.settings.connections["default"]}})
    engines = {"default": Pings(True), "lake": Pings(False), "broken": Raises()}
    result = health(replace(stores, settings=settings, engines=engines.get, catalogs=lambda: Pings(True)),
                    deep=True)
    assert statuses(result) == {"registry": "healthy", "models": "healthy", "connection:broken": "unavailable",
                                "connection:default": "healthy", "connection:lake": "unavailable",
                                "catalog": "healthy"}
    assert result["status"] == "degraded"                                    # tools still answer from metadata
    assert "hunter2" not in json.dumps(result)


@pytest.mark.parametrize("catalogs, expected", [
    (lambda: Raises(), "unavailable"),              # configured, does not answer
    (Raises(), "unavailable"),                      # cannot be built (a plugin is missing)
    (lambda: object(), "unknown"),                  # no ping to ask
    (lambda: None, "unknown"),                      # none configured
])
def test_the_catalog_check(stores, catalogs, expected):
    assert statuses(health(replace(stores, catalogs=catalogs), deep=True))["catalog"] == expected


def test_aggregate_status():
    def c(id, status):
        return HealthComponent(id, id, status, "")

    assert aggregate_status([c("registry", "healthy"), c("models", "healthy"), c("catalog", "unknown")]) == "healthy"
    assert aggregate_status([c("registry", "healthy"), c("models", "healthy"), c("x", "unavailable")]) == "degraded"
    assert aggregate_status([c("registry", "degraded"), c("models", "healthy")]) == "degraded"
    assert aggregate_status([c("registry", "healthy"), c("models", "unavailable")]) == "unavailable"
    assert aggregate_status([c("catalog", "unknown")]) == "unavailable"
    assert aggregate_status([]) == "unavailable"
