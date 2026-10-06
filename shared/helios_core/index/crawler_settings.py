"""Crawler settings versions in helios_index (CR-0e).

Saving validates the document and stores it as the next version number; saving
content identical to an existing version returns that version instead. Versions
are never changed. Activations are an append-only log; with none, crawls use
empty settings: the engine has no rules of its own (settings schema 2).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from ..crawler.settings import EMPTY_SETTINGS, CrawlerSettings
from .records import CrawlerSettingsActivationRecord, CrawlerSettingsRecord
from .store import IndexStore

VERSIONS = "helios_index.crawler_settings"
ACTIVATIONS = "helios_index.crawler_settings_activations"


class UnknownSettingsVersion(KeyError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def versions(store: IndexStore) -> list[CrawlerSettingsRecord]:
    records = [r for r in store.read(VERSIONS) if isinstance(r, CrawlerSettingsRecord)]
    return sorted(records, key=lambda r: r.version)


def save(
    store: IndexStore,
    settings: CrawlerSettings,
    created_by: str,
    note: str = "",
    clock: Callable[[], str] = _now,
) -> tuple[CrawlerSettingsRecord, bool]:
    """Store ``settings`` as a new version. Returns (record, created)."""
    existing = versions(store)
    content_hash = settings.content_hash()
    for record in existing:
        if record.content_hash == content_hash:
            return record, False
    record = CrawlerSettingsRecord(
        version=(existing[-1].version + 1) if existing else 1,
        content_hash=content_hash,
        settings_json=settings.canonical_json(),
        created_at=clock(),
        created_by=created_by,
        note=note[:2000],
    )
    store.append(VERSIONS, [record])
    return record, True


def get(store: IndexStore, version: int) -> CrawlerSettingsRecord:
    for record in store.read(VERSIONS, {"version": version}):
        assert isinstance(record, CrawlerSettingsRecord)
        return record
    raise UnknownSettingsVersion(version)


def settings_of(record: CrawlerSettingsRecord) -> CrawlerSettings:
    return CrawlerSettings.model_validate_json(record.settings_json)


def activate(
    store: IndexStore, version: int, activated_by: str, clock: Callable[[], str] = _now
) -> CrawlerSettingsActivationRecord:
    record = get(store, version)
    activation = CrawlerSettingsActivationRecord(
        version=record.version,
        content_hash=record.content_hash,
        activated_at=clock(),
        activated_by=activated_by,
    )
    store.append(ACTIVATIONS, [activation])
    return activation


def active(store: IndexStore) -> tuple[CrawlerSettingsRecord | None, CrawlerSettings]:
    """The active version and its settings; (None, empty settings) if none was activated."""
    activations = [
        r for r in store.read(ACTIVATIONS) if isinstance(r, CrawlerSettingsActivationRecord)
    ]
    if not activations:
        return None, EMPTY_SETTINGS
    latest = max(activations, key=lambda r: r.activated_at)
    record = get(store, latest.version)
    return record, settings_of(record)
