"""The model registry contract (burndown 4.1). Every backend runs this suite: add it to
BACKENDS (4.2 adds SQLAlchemy; 4.5 grows this into the conformance suite)."""
import threading
from datetime import datetime, timedelta, timezone

import pytest

from helios_core import Caller
from helios_core.domain import DataSource, DataSourceReference, Model
from helios_core.registry import (
    DRAFT,
    PUBLISHED,
    InMemoryRegistry,
    ModelRegistry,
    RegistryError,
    RevisionConflict,
    SemanticRevision,
)
from helios_core.visibility import visible_models

BACKENDS = {"memory": InMemoryRegistry}
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(params=sorted(BACKENDS))
def registry(request) -> ModelRegistry:
    return BACKENDS[request.param]()


def source(source_id="warehouse", name="Warehouse", **fields):
    return DataSource(source_id, name, "impala", "default", **fields)


def model(model_id="sales", name="Sales", sources=("warehouse",)):
    return Model(model_id, name, tuple(DataSourceReference(s) for s in sources))


def revision(revision_id="r1", model_id="sales", sha="a" * 64, published_at=T0, **fields):
    return SemanticRevision(revision_id, model_id, sha, f"models/{model_id}/{revision_id}.ossie.yaml",
                            published_at, 1024, **fields)


def test_data_sources_are_stored_listed_and_isolated(registry):
    registry.save_data_source(source("b", "beta", scope={"databases": ["sales"]}))
    registry.save_data_source(source("a", "Alpha"))
    assert [d.id for d in registry.data_sources()] == ["a", "b"]            # by name, case-insensitively
    fetched = registry.data_source("b")
    fetched.scope["databases"].append("hr")                                   # a copy: the registry is unchanged
    assert registry.data_source("b").scope == {"databases": ["sales"]}
    assert registry.data_source("missing") is None


def test_a_model_may_reference_only_known_data_sources(registry):
    with pytest.raises(RegistryError, match="unknown data source"):
        registry.save_model(model())
    registry.save_data_source(source())
    stored = registry.save_model(model(), created_by="alice")
    assert (stored.id, stored.status, stored.created_by) == ("sales", DRAFT, "alice")
    assert registry.model("sales") == model()
    assert registry.model("missing") is None and registry.registered_model("missing") is None


def test_saving_again_updates_the_model_but_keeps_its_creation(registry):
    registry.save_data_source(source())
    first = registry.save_model(model(), created_by="alice")
    renamed = Model("sales", "Sales (EMEA)", (DataSourceReference("warehouse", ("crm.orders",)),))
    second = registry.save_model(renamed, status=PUBLISHED, created_by="bob")
    assert (second.created_by, second.created_at) == ("alice", first.created_at)
    assert second.updated_at >= first.updated_at
    assert registry.registered_model("sales").model == renamed
    assert registry.registered_model("sales").status == PUBLISHED
    with pytest.raises(RegistryError, match="status"):
        registry.save_model(renamed, status=" ")


def test_models_are_listed_by_name_and_filtered_by_status(registry):
    registry.save_data_source(source())
    registry.save_model(model("m2", "beta"), status=PUBLISHED)
    registry.save_model(model("m1", "Alpha"))
    registry.save_model(model("m3", "gamma"), status=PUBLISHED)
    assert [m.id for m in registry.models()] == ["m1", "m2", "m3"]
    assert [m.id for m in registry.models(status=PUBLISHED)] == ["m2", "m3"]


def test_the_host_decides_which_registered_models_a_caller_sees(registry):
    registry.save_data_source(source())
    for model_id in ("finance", "sales"):
        registry.save_model(model(model_id, model_id.title()))

    class SalesOnly:
        def can_see(self, caller, model_id):
            return model_id == "sales" or caller.username == "cfo"

    ids = [m.id for m in registry.models()]
    assert visible_models(SalesOnly(), Caller("alice"), ids) == ["sales"]
    assert visible_models(SalesOnly(), Caller("cfo"), ids) == ["finance", "sales"]


def test_a_data_source_in_use_cannot_be_deleted(registry):
    registry.save_data_source(source())
    registry.save_data_source(source("spare", "Spare"))
    registry.save_model(model())
    with pytest.raises(RegistryError, match="used by model"):
        registry.delete_data_source("warehouse")
    assert registry.delete_data_source("spare") is True
    assert registry.delete_data_source("spare") is False
    assert [d.id for d in registry.data_sources()] == ["warehouse"]


def test_revisions_are_immutable_and_newest_first(registry):
    registry.save_data_source(source())
    with pytest.raises(RegistryError, match="unknown model"):
        registry.save_semantic_revision(revision())
    registry.save_model(model())

    first = registry.save_semantic_revision(revision("r1", published_by="alice"))
    assert registry.save_semantic_revision(revision("r1", published_by="retry", published_at=T0 + timedelta(1))) == first
    with pytest.raises(RevisionConflict):
        registry.save_semantic_revision(revision("r1", sha="b" * 64))

    registry.save_semantic_revision(revision("r3", published_at=T0 + timedelta(hours=1)))
    registry.save_semantic_revision(revision("r2", published_at=T0 + timedelta(hours=1)))
    assert [r.id for r in registry.semantic_revisions("sales")] == ["r3", "r2", "r1"]   # ties: by id, newest first
    assert registry.latest_semantic_revision("sales").id == "r3"
    assert registry.semantic_revision("r1") == first
    assert registry.latest_semantic_revision("other") is None and registry.semantic_revisions("other") == []


def test_concurrent_writers_all_land(registry):
    registry.save_data_source(source())

    def save(i):
        registry.save_model(model(f"m{i:02d}", f"Model {i:02d}"))

    threads = [threading.Thread(target=save, args=(i,)) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [m.id for m in registry.models()] == [f"m{i:02d}" for i in range(20)]
