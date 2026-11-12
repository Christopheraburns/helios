"""The ``Helios`` facade: one object for a developer's program.

    helios = Helios.from_settings()
    helios.models()                                         # the published models
    helios.search("revenue by region")                      # what a question involves
    helios.compile(["revenue"], ["shop.customers.c_region"]).sql
    helios.query(["revenue"], ["shop.customers.c_region"]).records()
    helios.publish("20261110T120000Z", accept_pending=True) # a discovery run's reviewed proposals

Each method calls the helios-serve tool of the same purpose (or helios-model's
``Publisher``) and raises ``HeliosError`` where the tool returns an error.

Who runs the queries: a facade built with no caller is one user's program, running with
the configured credentials, so a connection in ``service`` identity mode runs its
queries (helios-serve's ``single_user_policy``). ``Helios.from_settings(caller=...)`` or
``helios.as_caller(caller)`` act for that caller instead: the warehouse runs the query as
them (``impersonate`` or ``token`` mode) and a service-identity connection refuses, as in
every shared deployment. A host serving many users gives each request its own
``as_caller``, or uses helios-serve's tools directly.
"""
from __future__ import annotations

import dataclasses
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from helios_cortex.caller import Caller

if TYPE_CHECKING:
    from helios_cortex.settings import Settings
    from helios_model import Proposals, Publication
    from helios_serve import Registry, Stores


class HeliosError(Exception):
    """What a tool refused or could not do: ``code`` (helios-serve's error codes, such as
    ``invalid_semantic_query`` or ``model_not_found``), ``message`` and ``retryable``."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    dialect: str
    dataset: str                 # the fact table
    joins: tuple[str, ...]       # the other tables it joins
    columns: tuple[str, ...]


@dataclass(frozen=True)
class QueryResult:
    sql: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    dataset: str
    joins: tuple[str, ...]

    def records(self) -> list[dict[str, Any]]:
        """The rows as dicts by column name."""
        return [dict(zip(self.columns, row)) for row in self.rows]


def _list(value: str | Sequence[str] | None) -> list[str] | None:
    if value is None:
        return None
    return [value] if isinstance(value, str) else list(value)


class Helios:
    """Published semantic models, for one caller (None: this program's own user)."""

    def __init__(self, stores: Stores, *, caller: Caller | None = None, registry: Registry | None = None):
        self.stores = stores
        self.caller = caller
        self._registry = registry

    @classmethod
    def from_settings(cls, settings: Settings | None = None, *, profile: str | None = None,
                      config: str | os.PathLike[str] | None = None, caller: Caller | None = None,
                      **overrides: Any) -> Helios:
        """The registry and published models ``settings`` name (default: ``load_settings``
        with ``profile`` and ``config``, as the ``helios`` command reads them).
        ``overrides`` replace parts of helios-serve's ``Stores`` (``visibility``,
        ``data_policy``, ``audit``, ``engines``, ``catalogs``, ``registry``, ``models``)."""
        from helios_serve import Stores, single_user_policy

        if settings is None:
            from helios_cortex.settings import load_settings

            settings = load_settings(profile, config)
        elif profile is not None or config is not None:
            raise TypeError("pass settings, or profile/config to load them, not both")
        if caller is None and "data_policy" not in overrides:
            overrides["data_policy"] = single_user_policy()
        return cls(Stores.from_settings(settings, **overrides), caller=caller)

    def as_caller(self, caller: Caller | None) -> Helios:
        """The same models, acting for ``caller``: queries run as them, under the default
        data policy (the warehouse enforces their access) unless the host set its own."""
        from helios_cortex.data_authorization import DataPolicy
        from helios_serve.stores import SingleUserAuthorizer

        stores = self.stores
        if caller is not None and isinstance(stores.data_policy.platform, SingleUserAuthorizer):
            stores = dataclasses.replace(stores, data_policy=DataPolicy(restrictions=stores.data_policy.restrictions))
        return Helios(stores, caller=caller, registry=self._registry)

    @property
    def settings(self) -> Settings:
        return self.stores.settings

    @property
    def tools(self) -> Registry:
        """helios-serve's tool registry, for a program that offers the tools to an agent."""
        if self._registry is None:
            from helios_serve import load_registry

            self._registry = load_registry()
        return self._registry

    def _call(self, name: str, **arguments: Any) -> dict[str, Any]:
        from helios_serve import tools

        result = getattr(tools, name)(self.caller, self.stores, **arguments)
        if isinstance(result, dict) and result.get("error"):
            raise HeliosError(str(result["error"]), str(result.get("message") or result["error"]),
                              retryable=result.get("retryable") is True)
        return result

    # ------------------------------------------------------------------ reading
    def models(self) -> list[dict[str, Any]]:
        """The published models this caller may see: ``[{"name", "status"}]``."""
        return self._call("list_models")["models"]

    def describe(self, model: str | None = None) -> dict[str, Any]:
        """A model's datasets, fields, metrics and relationships (the first model by default)."""
        return self._call("describe_model", model=model)

    def search(self, question: str, *, limit: int = 10, model: str | None = None) -> list[dict[str, Any]]:
        """The datasets, fields and metrics ``question`` involves, best first."""
        return self._call("search_semantics", question=question, limit=limit, model=model)["matches"]

    def compile(self, metrics: str | Sequence[str], dimensions: str | Sequence[str] | None = None,
                filters: Sequence[dict[str, Any]] | None = None, *, limit: int = 100, dialect: str | None = None,
                model: str | None = None) -> CompiledQuery:
        """The SQL for ``metrics`` by ``dimensions`` (database.table.column), filtered by
        ``filters`` (``{"column", "op", "value"}``), in ``dialect`` (default: the model's
        own warehouse's)."""
        compiled = self._call("compile_query", metrics=_list(metrics), dimensions=_list(dimensions),
                              filters=list(filters) if filters is not None else None, limit=limit,
                              dialect=dialect, model=model)
        return CompiledQuery(compiled["sql"], compiled["dialect"], compiled["dataset"], tuple(compiled["joins"]),
                             tuple(compiled["columns"]))

    def query(self, metrics: str | Sequence[str], dimensions: str | Sequence[str] | None = None,
              filters: Sequence[dict[str, Any]] | None = None, *, limit: int = 100,
              model: str | None = None) -> QueryResult:
        """Compile and run on the model's warehouse, as this facade's caller. Values are
        as the engine returns them (``Decimal``, ``date``, ...)."""
        result = self._call("query_values", metrics=_list(metrics), dimensions=_list(dimensions),
                            filters=list(filters) if filters is not None else None, limit=limit, model=model)
        return QueryResult(result["sql"], tuple(result["columns"]), tuple(tuple(r) for r in result["rows"]),
                           result["dataset"], tuple(result["joins"]))

    def health(self, *, deep: bool = False) -> dict[str, Any]:
        """helios-serve's ``health``: the registry and the models, and with ``deep`` the
        warehouse connections and the catalog."""
        from helios_serve import health

        return health(self.stores, deep=deep)

    # ------------------------------------------------------------------ publishing
    def publish(self, proposals: Proposals | str, *, accept_pending: bool = False,
                min_confidence: float | None = None) -> Publication:
        """Publish a discovery run's reviewed proposals (``Proposals``, or the run id) as
        the model's new active revision, recorded as published by this facade's caller.
        ``accept_pending`` (or ``min_confidence``) first accepts the elements nobody has
        decided on, as ``helios publish --accept-pending`` does. The model must be in the
        registry. Raises HeliosError (``model_not_registered``, ``publication_invalid`` or
        ``discovery_failed``) before writing anything."""
        from helios_model import DiscoveryError, ModelNotRegistered, Proposer, Publisher
        from helios_model.publication import PublicationValidationError

        artifacts = getattr(self.stores.models, "artifacts", None)
        try:
            proposer = Proposer(self.settings, artifacts=artifacts)
            if isinstance(proposals, str):
                proposals = proposer.load(proposals)
            if accept_pending or min_confidence is not None:
                proposals.accept_all(min_confidence or 0.0)
                proposer.save_review(proposals, self.caller)
            publisher = Publisher(self.settings, registry=self.stores.registry, artifacts=artifacts)
            return publisher.publish(proposals, self.caller)
        except ModelNotRegistered as exc:
            raise HeliosError("model_not_registered", str(exc)) from exc
        except PublicationValidationError as exc:
            raise HeliosError("publication_invalid", str(exc)) from exc
        except DiscoveryError as exc:
            raise HeliosError("discovery_failed", str(exc)) from exc

    def __repr__(self) -> str:
        who = self.caller.username if self.caller is not None and self.caller.username else "this program's user"
        return f"Helios(caller={who!r})"
