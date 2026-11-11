"""The facades a host calls to build a model: harvest, profile, propose, publish.

    catalog = Catalog(settings)
    harvest = catalog.harvest(source, caller, databases=["sales"], model_id="sales")
    profile = Profiler(settings).profile(harvest, caller)
    proposals = Proposer(settings).propose(profile)
    proposals.accept_all(min_confidence=0.8)      # or a person reviews them
    publication = Publisher(settings).publish(proposals, caller)

Each stage keeps its document in the run store (``<run_id>/<stage>.json``) and returns
a result carrying the run id; the next stage takes that result or the run id, so the
stages can run in different processes. They are thin: the work is done by
``TableHarvester``, ``TableProfiler``, ``ModelProposer``, ``review`` and
``publication``, unchanged.

The ``caller`` (``helios_cortex.Caller``) is who the host says is asking. Harvest and
profile forward it to the engine, which runs the catalog reads and the profiling
queries as the caller under the connection's identity mode, so a model is built from
what that caller may see; ``None`` is the connection's service identity. Publish
records the caller's username as the publisher. Helios checks no permissions itself.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from helios_cortex.caller import Caller
from helios_cortex.catalog import catalog_for
from helios_cortex.domain import DataSource
from helios_cortex.engines import Engine, engine_for
from helios_cortex.registry import SemanticRevision
from helios_cortex.runs import RunStore
from helios_cortex.settings import Settings

from helios_model import publication as publishing
from helios_model.harvest import TableHarvester
from helios_model.profile import TableProfiler
from helios_model.proposals import Proposals
from helios_model.propose import ModelProposer

if TYPE_CHECKING:
    from helios_cortex.artifacts import ArtifactStore
    from helios_cortex.llm import LLMClient
    from helios_cortex.registry import ModelRegistry

LOGGER = logging.getLogger(__name__)


class DiscoveryError(RuntimeError):
    """A stage cannot run: its input run is missing, or nothing is configured to run it."""


class ModelNotRegistered(LookupError):
    """Publishing a model the registry does not hold. Register it first (``save_model``)."""


def new_run_id() -> str:
    """A run id: the UTC time to the second, so run ids sort by time."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _username(caller: Caller | None) -> str | None:
    return caller.username if caller is not None else None


def _frozen(document: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(document))


@dataclass(frozen=True)
class Harvest:
    """A harvest kept in run ``run_id``: tables, columns, row counts, glossary, query log."""

    run_id: str
    model_id: str
    data_source_id: str
    document: Mapping[str, Any] = field(repr=False)

    @property
    def tables(self) -> tuple[str, ...]:
        """``database.table`` for every harvested table."""
        return tuple(f"{t['database']}.{t['table']}" for t in self.document.get("tables", []))


@dataclass(frozen=True)
class Profile:
    """A profile kept in run ``run_id``: statistics, keys and verified relationships."""

    run_id: str
    model_id: str
    document: Mapping[str, Any] = field(repr=False)

    @property
    def relationships(self) -> tuple[Mapping[str, Any], ...]:
        """Relationships found from both names and data."""
        return tuple(self.document.get("relationships", []))

    @property
    def suggested_relationships(self) -> tuple[Mapping[str, Any], ...]:
        """Relationships the data supports but the names do not; the proposer decides them."""
        return tuple(self.document.get("suggested_relationships", []))


@dataclass(frozen=True)
class Publication:
    """A published model: the registry's revision row, the Ossie document and the
    artifact store's manifest (path, hash, size, counts, review)."""

    revision: SemanticRevision
    document: Mapping[str, Any] = field(repr=False)
    manifest: Mapping[str, Any] = field(repr=False)

    @property
    def model_id(self) -> str:
        return self.revision.model_id

    @property
    def revision_id(self) -> str:
        return self.revision.id


class _Stage:
    def __init__(self, settings: Settings, *, runs: RunStore | None = None):
        self.settings = settings
        self.runs = runs if runs is not None else RunStore.from_settings(settings)

    def _engine(self, connection_ref: str | None) -> Engine:
        connection = self.settings.connection(connection_ref)
        if connection is None:
            raise DiscoveryError(f"no connection {connection_ref!r} (and no default) in the settings")
        return engine_for(connection)

    def _load(self, run_id: str, stage: str, before: str) -> dict[str, Any]:
        document = self.runs.load(run_id, stage)
        if document is None:
            raise DiscoveryError(f"run {run_id!r} has no {stage}; {before} first")
        return document


class Catalog(_Stage):
    """Harvests a data source into a new run. ``Catalog(settings)`` keeps runs where the
    settings say (``RunStore.from_settings``); pass ``runs=`` to choose."""

    def harvest(self, source: DataSource, caller: Caller | None, *, databases: Sequence[str], model_id: str,
                glossaries: Sequence[str] | None = None, query_dir: str | None = None,
                catalog: str | None = None, run_id: str | None = None) -> Harvest:
        """Harvest ``databases`` of ``source`` as ``caller`` for model ``model_id``.

        The engine is the connection ``source.connection_ref`` names; the glossary comes
        from the catalog ``catalog`` names (the default catalog when None, none when the
        settings have no catalogs). ``databases`` is required: Helios never guesses what
        to harvest. ``run_id`` defaults to a new one; an existing run is never replaced."""
        databases = [d for d in databases if d]
        if not databases:
            raise ValueError("name at least one database to harvest")
        run_id = run_id or new_run_id()
        if self.runs.load(run_id, "harvest") is not None:
            raise DiscoveryError(f"run {run_id!r} already has a harvest; start a new run")
        engine = self._engine(source.connection_ref)
        harvester = TableHarvester(engine, catalog_for(self.settings.catalog(catalog)))
        document = harvester.run(list(databases), list(glossaries) if glossaries else None, query_dir, caller=caller)
        document.update({"model_id": model_id, "data_source_id": source.id,
                         "connection_ref": source.connection_ref, "harvested_by": _username(caller)})
        self.runs.write(run_id, "harvest", document)
        return Harvest(run_id, model_id, source.id, _frozen(document))


class Profiler(_Stage):
    """Profiles a harvest on the connection it was harvested from."""

    def __init__(self, settings: Settings, *, runs: RunStore | None = None,
                 overlap_threshold: float = 0.95, max_pairs: int = 400):
        super().__init__(settings, runs=runs)
        self.overlap_threshold = overlap_threshold
        self.max_pairs = max_pairs

    def profile(self, harvest: Harvest | str, caller: Caller | None = None) -> Profile:
        """Profile the harvest (a ``Harvest`` or its run id) as ``caller``, or as the
        service identity without one, and keep the result in the same run."""
        run_id = harvest.run_id if isinstance(harvest, Harvest) else harvest
        document = self._load(run_id, "harvest", "harvest")
        if "connection_ref" not in document:
            raise DiscoveryError(f"run {run_id!r} was not harvested by Catalog.harvest; it names no connection")
        profiler = TableProfiler(self._engine(document["connection_ref"]), self.overlap_threshold, self.max_pairs)
        result = profiler.run(document, caller=caller)
        result.update({"model_id": document.get("model_id"), "profiled_by": _username(caller)})
        self.runs.write(run_id, "profile", result)
        return Profile(run_id, result["model_id"], _frozen(result))


class Proposer(_Stage):
    """Drafts a model from a profiled run with an LLM: ``llm=`` or the settings' ``[llm]``."""

    def __init__(self, settings: Settings, *, runs: RunStore | None = None,
                 artifacts: ArtifactStore | None = None, llm: LLMClient | None = None):
        super().__init__(settings, runs=runs)
        self._artifacts = artifacts
        self._llm = llm

    @property
    def artifacts(self) -> ArtifactStore:
        if self._artifacts is None:
            from helios_cortex.artifacts import ArtifactStore

            self._artifacts = ArtifactStore.from_settings(self.settings)
        return self._artifacts

    def _client(self) -> LLMClient:
        if self._llm is None:
            unconfigured = DiscoveryError("no LLM configured: set [llm] provider (and its key or base URL)")
            if self.settings.llm.provider is None:
                raise unconfigured          # before importing the client, which needs helios-cortex[llm]
            from helios_cortex.llm import llm_from_settings

            self._llm = llm_from_settings(self.settings.llm)
            if self._llm is None:
                raise unconfigured
        return self._llm

    def propose(self, profile: Profile | str, *, tables: Sequence[str] | None = None) -> Proposals:
        """Propose a model from the run's harvest and profile, with no decisions yet: all
        the harvested tables, or only ``tables`` (``database.table``). The proposal is kept
        in the run and as the model's latest proposal in the artifact store."""
        run_id = profile.run_id if isinstance(profile, Profile) else profile
        harvest = self._load(run_id, "harvest", "harvest")
        if tables:
            harvested = {f"{t['database']}.{t['table']}": t for t in harvest.get("tables", [])}
            unknown = sorted(set(tables) - set(harvested))
            if unknown:
                raise DiscoveryError(f"run {run_id!r} did not harvest {', '.join(unknown)}")
            harvest = {**harvest, "tables": [harvested[t] for t in harvested if t in set(tables)]}
        profiled = self._load(run_id, "profile", "profile")
        model_id = harvest.get("model_id")
        if not model_id:
            raise DiscoveryError(f"run {run_id!r} names no model")
        proposal = ModelProposer(self._client()).run(harvest, profiled)
        proposal["model_id"] = model_id
        self.runs.write(run_id, "propose", proposal)
        self.artifacts.write_proposal(model_id, run_id, proposal)
        return Proposals(proposal, run_id=run_id, model_id=model_id)

    def load(self, run_id: str) -> Proposals:
        """A run's proposals with the decisions made on them so far."""
        proposal = self._load(run_id, "propose", "propose")
        model_id = proposal.get("model_id") or (self.runs.load(run_id, "harvest") or {}).get("model_id")
        if not model_id:
            raise DiscoveryError(f"run {run_id!r} names no model")
        return Proposals(proposal, run_id=run_id, model_id=model_id, review=self.runs.load(run_id, "review"))

    def save_review(self, proposals: Proposals, caller: Caller | None, *, at: datetime | None = None) -> None:
        """Keep the decisions on ``proposals`` in their run, as reviewed by ``caller`` at
        ``at`` (now by default). Who and when are stamped on ``proposals`` too, and
        published with them."""
        proposals.mark_reviewed(_username(caller), at)
        self.runs.write(proposals.run_id, "review", proposals.review)


class Publisher:
    """Publishes reviewed proposals: the Ossie artifact (validated, then made the model's
    active revision) and its row in the registry."""

    def __init__(self, settings: Settings, *, registry: ModelRegistry | None = None,
                 artifacts: ArtifactStore | None = None, runs: RunStore | None = None):
        self.settings = settings
        if registry is None:
            from helios_cortex.registry import SqlRegistry

            registry = SqlRegistry.from_settings(settings)
        if artifacts is None:
            from helios_cortex.artifacts import ArtifactStore

            artifacts = ArtifactStore.from_settings(settings)
        self.registry = registry
        self.artifacts = artifacts
        self.runs = runs if runs is not None else RunStore.from_settings(settings)

    def publish(self, proposals: Proposals, caller: Caller | None) -> Publication:
        """Publish the accepted and edited elements of ``proposals``; pending and rejected
        ones are left out. The decisions are kept in the run, so the run records what was
        published. Raises ModelNotRegistered, or ``publication.PublicationValidationError``
        when the reviewed model is not valid Ossie, before writing anything."""
        model_id = proposals.model_id
        if self.registry.model(model_id) is None:
            raise ModelNotRegistered(f"model {model_id!r} is not in the registry; register it before publishing")
        harvest = self.runs.load(proposals.run_id, "harvest") or {}
        proposal, review = proposals.proposal, proposals.review
        published_by = _username(caller)
        result = publishing.publish_reviewed_proposal(   # validates before it writes
            self.artifacts, model_id=model_id, model_name=publishing.model_name_for_proposal(proposal, harvest),
            run_id=proposals.run_id, proposal=proposal, review=review, published_by=published_by)
        self.runs.write(proposals.run_id, "review", review)
        manifest = result.manifest
        revision = self.registry.save_semantic_revision(SemanticRevision(
            id=manifest["revision_id"], model_id=model_id, sha256=manifest["sha256"],
            artifact_path=manifest["path"], ossie_version=manifest.get("ossie_version"),
            discovery_run_id=proposals.run_id, published_at=datetime.fromisoformat(manifest["published_at"]),
            published_by=published_by, size_bytes=manifest["size_bytes"]))
        LOGGER.info("registered revision %s of model %s", revision.id, model_id)
        return Publication(revision, _frozen(result.document), _frozen(manifest))
