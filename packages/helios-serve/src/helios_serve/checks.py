"""Whether a deployment can answer: ``health(stores) -> dict`` (exported as ``helios_serve.health``).

    health(stores)               the registry and the published models
    health(stores, deep=True)    also each configured warehouse connection and the catalog

A host serves the result wherever it reports health (an HTTP ``/healthz``, a Daedalus
probe, a CLI). Each check runs on its own, so one failed dependency does not hide the
rest, and a check's description is a fixed sentence: an exception's text (which can
carry a host name, a URL or a token) never reaches the result or the log.

The result::

    {"status": "healthy" | "degraded" | "unavailable",
     "version": "<helios-serve version>",
     "model_count": 3,                        # published models; None when unreadable
     "components": [{"id", "label", "status", "description"}, ...]}

A component is ``healthy``, ``degraded`` (reachable but not ready, such as a registry
schema that needs an upgrade), ``unavailable`` or ``unknown`` (not checked: nothing
configured). The deployment is ``unavailable`` when the registry or the models are,
since no tool can answer then; ``degraded`` when any checked component is not healthy;
``healthy`` otherwise. ``unknown`` components do not count.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Literal

from helios_serve.stores import Stores

LOGGER = logging.getLogger(__name__)

HealthState = Literal["healthy", "degraded", "unavailable", "unknown"]
ESSENTIAL = frozenset({"registry", "models"})   # without these no tool can answer
_PROBE = "__helios_health__"                    # a data-source id no deployment uses


@dataclass(frozen=True)
class HealthComponent:
    id: str
    label: str
    status: HealthState
    description: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def aggregate_status(components: list[HealthComponent]) -> HealthState:
    """``unavailable`` when an essential component is; ``degraded`` when any checked
    component is not healthy; ``healthy`` otherwise (``unknown`` ones are skipped)."""
    if any(c.id in ESSENTIAL and c.status == "unavailable" for c in components):
        return "unavailable"
    checked = [c for c in components if c.status != "unknown"]
    if not checked:
        return "unavailable"
    return "healthy" if all(c.status == "healthy" for c in checked) else "degraded"


def _failed(component_id: str, exc: BaseException) -> None:
    # The type only: the message may name a host or carry a credential.
    LOGGER.warning("health check %s failed: %s", component_id, type(exc).__name__)


def _registry(stores: Stores) -> HealthComponent:
    from helios_cortex.registry import RegistryError

    label = "Model registry"
    try:
        stores.registry.data_source(_PROBE)        # one read, on any registry backend
    except RegistryError as exc:                   # RegistryNotReady: reachable, schema not current
        _failed("registry", exc)
        return HealthComponent("registry", label, "degraded",
                               "The model registry is reachable but its schema is not current; "
                               "run `helios registry upgrade`.")
    except Exception as exc:
        _failed("registry", exc)
        return HealthComponent("registry", label, "unavailable", "The model registry could not be read.")
    return HealthComponent("registry", label, "healthy", "The model registry is readable.")


def _models(stores: Stores) -> tuple[HealthComponent, int | None]:
    label = "Published models"
    try:
        count = len(stores.models.sources())
    except Exception as exc:
        _failed("models", exc)
        return HealthComponent("models", label, "unavailable", "The published models could not be listed."), None
    if count == 0:
        return HealthComponent("models", label, "degraded", "No model is published yet."), 0
    return HealthComponent("models", label, "healthy",
                           f"{count} published model{'s' if count != 1 else ''}."), count


def _probe(component_id: str, label: str, build: Callable[[], Any], what: str) -> HealthComponent:
    try:
        target = build()
        if target is None:
            return HealthComponent(component_id, label, "unknown", f"{what} is not configured.")
        if target.ping():
            return HealthComponent(component_id, label, "healthy", f"{what} responded.")
    except Exception as exc:
        _failed(component_id, exc)
    return HealthComponent(component_id, label, "unavailable", f"{what} did not respond.")


def _connections(stores: Stores) -> list[HealthComponent]:
    return [_probe(f"connection:{name}", f"Warehouse connection {name}",
                   lambda name=name: stores.engines(name), f"The {name} warehouse connection")
            for name in sorted(stores.settings.connections)]


def _catalog(stores: Stores) -> HealthComponent:
    label = "Data catalog"
    try:
        catalog = stores.catalogs()
    except Exception as exc:                       # a missing plugin, bad options
        _failed("catalog", exc)
        return HealthComponent("catalog", label, "unavailable", "The data catalog could not be built.")
    if catalog is not None and not callable(getattr(catalog, "ping", None)):
        return HealthComponent("catalog", label, "unknown", "The data catalog has no health check.")
    return _probe("catalog", label, lambda: catalog, "The data catalog")


def health(stores: Stores, *, deep: bool = False) -> dict[str, Any]:
    """The deployment's health: the registry and the published models, and with ``deep``
    each configured warehouse connection and the catalog (each pinged with the service's
    own credentials, so a deep check costs a round trip per connection)."""
    import helios_serve

    components = [_registry(stores)]
    models, count = _models(stores)
    components.append(models)
    if deep:
        components += _connections(stores)
        components.append(_catalog(stores))
    return {"status": aggregate_status(components), "version": helios_serve.__version__, "model_count": count,
            "components": [c.to_dict() for c in components]}
