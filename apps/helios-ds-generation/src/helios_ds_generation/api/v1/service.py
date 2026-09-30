"""The API's generation service: lakehouse job store + dispatcher.

Built lazily from the environment on first use, so the Application starts (and
serves the dashboard) even before the lakehouse is configured; job endpoints
then return 503 with the reason. Tests replace it through ``get_service``.

Environment: HELIOS_DS_LAKEHOUSE (impala), HELIOS_DS_DISPATCHER (workbench),
HELIOS_DS_API_KEY, plus the Impala settings.
"""

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from fastapi import HTTPException

from helios_ds.backends import lakehouse_from_uri
from helios_ds.catalog import DatasetCatalog
from helios_ds.jobs import Dispatcher, JobStore, dispatcher_from_env
from helios_ds.object_store import ObjectStore, store_from_locator
from helios_ds.review import ReviewStore


@dataclass
class GenerationService:
    jobs: JobStore
    dispatcher: Dispatcher
    datasets: DatasetCatalog = field(default=None)  # type: ignore[assignment]
    reviews: ReviewStore = field(default=None)  # type: ignore[assignment]
    # (locator, logical key) -> the object store that wrote it; approvals
    # re-validate a dataset against the store it was published to.
    open_store: Callable[[Dict[str, Any], str], ObjectStore] = store_from_locator

    def __post_init__(self) -> None:
        if self.datasets is None:
            self.datasets = DatasetCatalog(self.jobs.sink)
        if self.reviews is None:
            self.reviews = ReviewStore(self.jobs.sink)


_service: Optional[GenerationService] = None
_lock = threading.Lock()


def get_service() -> GenerationService:
    global _service
    with _lock:
        if _service is None:
            try:
                _service = GenerationService(JobStore(lakehouse_from_uri()), dispatcher_from_env())
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail=f"generation service is not configured: {type(exc).__name__}: {exc}",
                ) from exc
        return _service
