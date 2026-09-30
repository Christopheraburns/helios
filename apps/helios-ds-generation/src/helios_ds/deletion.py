"""Deleting a dataset the project no longer needs.

Removes the dataset's objects (manifest and artifacts) and its rows in
helios_ds and helios_ground_truth. The lifecycle log is kept and gains a
DELETED event (who, when, why), so the audit trail survives; generation runs
and job history are kept too. Generating the same config again recreates the
dataset under the same ID.

Order: the DELETED event first (the dataset stops being crawler-visible at
once), then objects, then rows, with helios_ds.datasets last. If anything fails
part-way, deleting again finishes the job.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .lakehouse import LakehouseSink
from .lakehouse.tables import TABLES
from .lifecycle import DatasetLifecycle
from .manifests import manifest_key
from .object_store import MANIFEST_PREFIX, ObjectStore
from .render.dataset import dataset_objects_prefix
from .schemas import DatasetRecord

# Kept: the audit trail and run/job history.
KEPT_TABLES = frozenset(
    {
        "helios_ds.dataset_lifecycle",
        "helios_ds.generation_runs",
        "helios_ds.generation_jobs",
        "helios_ds.job_events",
    }
)
# Every other table keyed by dataset, with helios_ds.datasets last: while its row
# exists the dataset stays listed, so an interrupted deletion can be retried.
PURGED_TABLES: List[str] = sorted(
    (
        name
        for name, spec in TABLES.items()
        if name not in KEPT_TABLES and "dataset_id" in spec.model.model_fields
    ),
    key=lambda name: (name == "helios_ds.datasets", name),
)


class DatasetGone(KeyError):
    """No datasets row: never published, or already deleted."""


@dataclass
class DeletionResult:
    dataset_id: str
    objects_deleted: int
    tables_purged: List[str] = field(default_factory=list)
    already_marked: bool = False


def delete_dataset(
    sink: LakehouseSink,
    open_store: Callable[[Dict[str, Any], str], ObjectStore],
    dataset_id: str,
    actor: str,
    reason: Optional[str] = None,
) -> DeletionResult:
    records = sink.read_dataset("helios_ds.datasets", dataset_id)
    if not records:
        raise DatasetGone(dataset_id)
    record = records[0]
    assert isinstance(record, DatasetRecord)

    event = DatasetLifecycle(sink).mark_deleted(dataset_id, actor, reason)
    store = open_store(record.manifest_locator, manifest_key(dataset_id))
    objects = store.delete_prefix(f"{MANIFEST_PREFIX}/{dataset_id}")
    objects += store.delete_prefix(dataset_objects_prefix(dataset_id))
    for table in PURGED_TABLES:
        sink.delete_dataset_rows(table, dataset_id)
    return DeletionResult(dataset_id, objects, list(PURGED_TABLES), already_marked=event is None)
