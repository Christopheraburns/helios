"""The backend conformance suite: the tests every registry and artifact storage backend
must pass, Helios's own and anyone else's.

A backend's tests subclass a suite and provide one fixture that returns a fresh, empty
backend; pytest collects the inherited tests:

    import pytest
    from helios_core.conformance import ArtifactStorageConformance, RegistryConformance

    class TestMyRegistry(RegistryConformance):
        @pytest.fixture
        def registry(self):
            return MyRegistry(...)            # a ModelRegistry with no records

    class TestMyStorage(ArtifactStorageConformance):
        @pytest.fixture
        def storage(self, tmp_path):
            return MyStorage(...)             # an ArtifactStorage with no keys

``RegistryConformance`` checks the ModelRegistry rules (helios_core.registry).
``ArtifactStorageConformance`` checks the storage operations, above all that ``create``
is atomic under racing writers, and then runs the ArtifactStore (immutable revisions,
the activation log) and RunStore on the backend, since those are built on it.

Needs pytest (``pip install 'helios-core[testing]'``).
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from helios_core._optional import missing_extra

try:
    import pytest
except ImportError as exc:
    raise missing_extra("the conformance suite", "testing", exc) from exc

from helios_core.artifacts import ActivationConflict, ArtifactError, ArtifactStore
from helios_core.caller import Caller
from helios_core.domain import DataSource, DataSourceReference, Model
from helios_core.ossie import SemanticModel
from helios_core.registry import (
    DRAFT,
    PUBLISHED,
    RegistryError,
    RevisionConflict,
    SemanticRevision,
)
from helios_core.visibility import visible_models

__all__ = ["ArtifactStorageConformance", "RegistryConformance"]

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
RACERS = 10


def _race(count, action):
    """Run ``action(i)`` on ``count`` threads released together; the results by i."""
    results = {}
    barrier = threading.Barrier(count)

    def run(i):
        barrier.wait()
        results[i] = action(i)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


# ---------------------------------------------------------------- the model registry

def _source(source_id="warehouse", name="Warehouse", **fields):
    return DataSource(source_id, name, "impala", "default", **fields)


def _model(model_id="sales", name="Sales", sources=("warehouse",)):
    return Model(model_id, name, tuple(DataSourceReference(s) for s in sources))


def _revision(revision_id="r1", model_id="sales", sha="a" * 64, published_at=T0, **fields):
    return SemanticRevision(revision_id, model_id, sha, f"models/{model_id}/{revision_id}.ossie.yaml",
                            published_at, 1024, **fields)


class RegistryConformance:
    """The ModelRegistry contract. Subclasses provide a ``registry`` fixture: an empty registry."""

    @pytest.fixture
    def registry(self):
        raise NotImplementedError("a conformance subclass provides the registry fixture")

    def test_data_sources_are_stored_listed_and_isolated(self, registry):
        registry.save_data_source(_source("b", "beta", scope={"databases": ["sales"]}))
        registry.save_data_source(_source("a", "Alpha"))
        assert [d.id for d in registry.data_sources()] == ["a", "b"]           # by name, case-insensitively
        fetched = registry.data_source("b")
        fetched.scope["databases"].append("hr")                                  # a copy: the registry is unchanged
        assert registry.data_source("b").scope == {"databases": ["sales"]}
        assert registry.data_source("missing") is None

    def test_saving_a_data_source_again_replaces_it(self, registry):
        registry.save_data_source(_source())
        registry.save_data_source(_source(name="Warehouse (prod)", scope={"databases": ["crm"]}))
        assert [d.name for d in registry.data_sources()] == ["Warehouse (prod)"]
        assert registry.data_source("warehouse").scope == {"databases": ["crm"]}

    def test_a_model_may_reference_only_known_data_sources(self, registry):
        with pytest.raises(RegistryError, match="unknown data source"):
            registry.save_model(_model())
        assert registry.models() == []
        registry.save_data_source(_source())
        stored = registry.save_model(_model(), created_by="alice")
        assert (stored.id, stored.status, stored.created_by) == ("sales", DRAFT, "alice")
        assert registry.model("sales") == _model()
        assert registry.model("missing") is None and registry.registered_model("missing") is None

    def test_saving_again_updates_the_model_but_keeps_its_creation(self, registry):
        registry.save_data_source(_source())
        first = registry.save_model(_model(), created_by="alice")
        renamed = Model("sales", "Sales (EMEA)", (DataSourceReference("warehouse", ("crm.orders",)),))
        second = registry.save_model(renamed, status=PUBLISHED, created_by="bob")
        assert (second.created_by, second.created_at) == ("alice", first.created_at)
        assert second.updated_at >= first.updated_at
        assert first.created_at.tzinfo is not None and second.updated_at.tzinfo is not None
        assert registry.registered_model("sales").model == renamed
        assert registry.registered_model("sales").status == PUBLISHED
        with pytest.raises(RegistryError, match="status"):
            registry.save_model(renamed, status=" ")

    def test_models_are_listed_by_name_and_filtered_by_status(self, registry):
        registry.save_data_source(_source())
        registry.save_model(_model("m2", "beta"), status=PUBLISHED)
        registry.save_model(_model("m1", "Alpha"))
        registry.save_model(_model("m3", "gamma"), status=PUBLISHED)
        assert [m.id for m in registry.models()] == ["m1", "m2", "m3"]
        assert [m.id for m in registry.models(status=PUBLISHED)] == ["m2", "m3"]
        assert [m.id for m in registry.models(status=DRAFT)] == ["m1"]

    def test_the_host_decides_which_registered_models_a_caller_sees(self, registry):
        registry.save_data_source(_source())
        for model_id in ("finance", "sales"):
            registry.save_model(_model(model_id, model_id.title()))

        class SalesOnly:
            def can_see(self, caller, model_id):
                return model_id == "sales" or caller.username == "cfo"

        ids = [m.id for m in registry.models()]
        assert visible_models(SalesOnly(), Caller("alice"), ids) == ["sales"]
        assert visible_models(SalesOnly(), Caller("cfo"), ids) == ["finance", "sales"]

    def test_a_data_source_in_use_cannot_be_deleted(self, registry):
        registry.save_data_source(_source())
        registry.save_data_source(_source("spare", "Spare"))
        registry.save_model(_model())
        with pytest.raises(RegistryError, match="used by model"):
            registry.delete_data_source("warehouse")
        assert registry.delete_data_source("spare") is True
        assert registry.delete_data_source("spare") is False
        assert [d.id for d in registry.data_sources()] == ["warehouse"]

    def test_revisions_are_immutable_and_newest_first(self, registry):
        registry.save_data_source(_source())
        with pytest.raises(RegistryError, match="unknown model"):
            registry.save_semantic_revision(_revision())
        registry.save_model(_model())

        first = registry.save_semantic_revision(_revision("r1", published_by="alice"))
        retry = _revision("r1", published_by="retry", published_at=T0 + timedelta(1))
        assert registry.save_semantic_revision(retry) == first
        with pytest.raises(RevisionConflict):
            registry.save_semantic_revision(_revision("r1", sha="b" * 64))

        registry.save_semantic_revision(_revision("r3", published_at=T0 + timedelta(hours=1)))
        registry.save_semantic_revision(_revision("r2", published_at=T0 + timedelta(hours=1)))
        assert [r.id for r in registry.semantic_revisions("sales")] == ["r3", "r2", "r1"]  # ties: by id
        assert registry.latest_semantic_revision("sales").id == "r3"
        assert registry.semantic_revision("r1") == first
        assert registry.semantic_revision("r1").published_at == T0
        assert registry.latest_semantic_revision("other") is None
        assert registry.semantic_revisions("other") == []

    def test_concurrent_writers_all_land(self, registry):
        registry.save_data_source(_source())
        _race(20, lambda i: registry.save_model(_model(f"m{i:02d}", f"Model {i:02d}")))
        assert [m.id for m in registry.models()] == [f"m{i:02d}" for i in range(20)]

    def test_racing_saves_of_one_revision_store_it_once(self, registry):
        registry.save_data_source(_source())
        registry.save_model(_model())
        _race(RACERS, lambda i: registry.save_semantic_revision(_revision("r1", published_by=f"writer {i}")))
        assert [r.id for r in registry.semantic_revisions("sales")] == ["r1"]


# ---------------------------------------------------------------- artifact storage

def _ossie(version):
    return {"version": "0.1.0", "name": f"sales v{version}", "datasets": [], "relationships": [], "metrics": [],
            "custom_extensions": [{"vendor_name": "HELIOS", "data": '{"model_id": "sales"}'}]}


def _yaml_text(version):
    return f"version: 0.1.0\nname: sales v{version}\ndatasets: []\nrelationships: []\nmetrics: []\n"


class ArtifactStorageConformance:
    """The ArtifactStorage contract, and the ArtifactStore and RunStore built on it.
    Subclasses provide a ``storage`` fixture: storage with no keys under its root."""

    @pytest.fixture
    def storage(self):
        raise NotImplementedError("a conformance subclass provides the storage fixture")

    # ---------------------------------------------------------------- the storage operations

    def test_keys_are_written_read_and_listed(self, storage):
        assert not storage.exists("models/a/x.json")
        with pytest.raises(FileNotFoundError):
            storage.read_bytes("models/a/x.json")
        assert storage.list("models") == []
        storage.write_bytes("models/a/x.json", b"one")
        storage.write_bytes("models/a/x.json", b"two")
        storage.write_bytes("models/b/y.json", b"y")
        assert storage.read_bytes("models/a/x.json") == b"two"
        assert storage.exists("models/a/x.json")
        assert storage.list("models") == ["a", "b"]
        assert storage.list("models/a") == ["x.json"]

    def test_content_round_trips_byte_for_byte(self, storage):
        content = bytes(range(256)) * 64 + "naïve ✓\r\n".encode()
        storage.write_bytes("models/a/blob.bin", content)
        storage.write_bytes("models/a/empty.json", b"")
        assert storage.read_bytes("models/a/blob.bin") == content
        assert storage.read_bytes("models/a/empty.json") == b""

    def test_listing_stops_at_the_prefix_boundary(self, storage):
        storage.write_bytes("models/sales/x.json", b"x")
        storage.write_bytes("models/sales-eu/y.json", b"y")
        storage.write_bytes("models/sales/deep/z.json", b"z")
        assert storage.list("models/sales") == ["deep", "x.json"]
        assert storage.list("models/sal") == []

    def test_create_writes_only_when_the_key_is_absent(self, storage):
        assert storage.create("models/a/once.json", b"first") is True
        assert storage.create("models/a/once.json", b"second") is False
        assert storage.read_bytes("models/a/once.json") == b"first"

    def test_exactly_one_of_many_racing_creates_wins(self, storage):
        results = _race(RACERS, lambda i: storage.create("models/a/race.json", f"writer {i}".encode()))
        winners = [i for i, won in results.items() if won]
        assert len(winners) == 1
        assert storage.read_bytes("models/a/race.json") == f"writer {winners[0]}".encode()

    def test_keys_cannot_escape_the_root(self, storage):
        for key in ("../outside", "/etc/passwd", "models/../../x"):
            with pytest.raises(ValueError, match="invalid artifact key"):
                storage.write_bytes(key, b"x")

    # ---------------------------------------------------------------- the artifact store on it

    def test_publishing_activates_an_immutable_revision(self, storage):
        store = ArtifactStore(storage)
        assert store.active_revision("sales") is None and store.published_model_ids() == []

        store.write_published_ossie("sales", _ossie(1), _yaml_text(1), {"published_by": "alice"},
                                    activated_by="alice")
        first = store.active_revision("sales")
        store.write_published_ossie("sales", _ossie(2), _yaml_text(2), {}, activated_by="bob")
        second = store.active_revision("sales")

        assert (first.sequence, second.sequence, second.previous_revision_id) == (1, 2, first.revision_id)
        assert [a.activated_by for a in store.activations("sales")] == ["bob", "alice"]
        assert store.read_published_ossie("sales") == _yaml_text(2)
        assert store.read_revision("sales", first.revision_id, "yaml") == _yaml_text(1)
        assert SemanticModel.load_published("sales", store).name == "sales v2"
        assert store.published_model_ids() == ["sales"]

        # Rolling back is activating an older revision; it is a new entry in the log.
        store.activate("sales", first.revision_id, activated_by="carol")
        assert store.read_published_ossie("sales") == _yaml_text(1)
        assert [a.sequence for a in store.activations("sales")] == [3, 2, 1]

    def test_a_stale_or_racing_activation_is_a_conflict(self, storage):
        store = ArtifactStore(storage)
        store.write_published_ossie("sales", _ossie(1), _yaml_text(1), {})
        revision = store.active_revision("sales").revision_id
        with pytest.raises(ActivationConflict, match="at activation 1, not None"):
            store.activate("sales", revision, expected_sequence=None)
        with pytest.raises(ArtifactError, match="does not exist"):
            store.activate("sales", "0" * 64)

        def activate(_):
            try:
                store.activate("sales", revision, expected_sequence=1)
                return "won"
            except ActivationConflict:
                return "conflict"

        outcomes = _race(8, activate).values()
        assert sorted(outcomes) == ["conflict"] * 7 + ["won"]
        assert store.active_revision("sales").sequence == 2

    def test_racing_publishers_each_get_their_own_activation(self, storage):
        store = ArtifactStore(storage)
        _race(4, lambda i: store.write_published_ossie("sales", _ossie(i), _yaml_text(i), {},
                                                         activated_by=f"writer {i}"))
        log = store.activations("sales")
        assert [a.sequence for a in log] == [4, 3, 2, 1]
        assert sorted(a.activated_by for a in log) == [f"writer {i}" for i in range(4)]
        assert store.read_published_ossie("sales") == store.read_revision("sales", log[0].revision_id, "yaml")

    def test_a_published_revision_cannot_be_rewritten(self, storage):
        store = ArtifactStore(storage)
        store.write_published_ossie("sales", _ossie(1), _yaml_text(1), {})
        revision = store.active_revision("sales").revision_id
        storage.write_bytes(f"models/sales/revisions/{revision}/semantic.ossie.json", b'{"tampered": true}\n')
        with pytest.raises(ArtifactError, match="hash verification failed"):
            store.read_published_ossie("sales", "json")
        with pytest.raises(ArtifactError, match="immutable semantic revision was modified"):
            store.write_revision("sales", _ossie(1), _yaml_text(1), {})

    def test_local_paths_exist_only_for_a_local_directory(self, storage):
        store = ArtifactStore(storage)
        store.write_proposal("sales", "run1", {"tables": []})
        assert store.read_json("sales", "proposed", "run1.proposal.json")["model_id"] == "sales"
        if storage.local_path("") is None:
            assert store.root is None
            with pytest.raises(ArtifactError, match="object storage"):
                store.directory("sales", "proposed")
            assert str(store.location("models/sales/proposed/run1.proposal.json")).startswith(storage.url)
        else:
            assert store.directory("sales", "proposed").is_dir()

    # ---------------------------------------------------------------- discovery runs on it

    def test_runs_live_with_the_artifacts(self, storage):
        runs = ArtifactStore(storage).runs
        assert runs.run_ids() == [] and runs.latest_run_id() is None and runs.list_runs() == []
        runs.write("20261101T120000Z", "harvest", {"model_id": "sales", "harvested_at": "2026-11-01T12:00:00+00:00",
                                                   "engine": "duckdb", "databases": ["sales"], "tables": []})
        runs.write("20261102T090000Z", "harvest", {"model_id": "sales", "harvested_at": "2026-11-02T09:00:00+00:00",
                                                   "tables": []})
        runs.write("20261102T090000Z", "propose", {"model_id": "sales",
                                                   "proposed_at": "2026-11-02T09:05:00+00:00"})

        assert runs.run_ids() == ["20261102T090000Z", "20261101T120000Z"]
        assert runs.latest_run_id() == "20261102T090000Z"
        assert runs.load("20261101T120000Z", "harvest")["engine"] == "duckdb"
        assert runs.load("20261101T120000Z", "profile") is None
        assert runs.summary("20261102T090000Z")["propose"] is True
        assert runs.summary("20261101T120000Z")["propose"] is False
        listed = runs.list_runs()
        assert [r["id"] for r in listed] == ["20261102T090000Z", "20261101T120000Z"]
        assert all(r["updated"] for r in listed)
        assert storage.exists("runs/20261101T120000Z/harvest.json")     # beside models/
        with pytest.raises(ValueError, match="run_id"):
            runs.write("../escape", "harvest", {})
        with pytest.raises(ValueError, match="unknown run document"):
            runs.write("20261101T120000Z", "notes", {})
