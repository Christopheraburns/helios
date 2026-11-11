"""Plugins: what other packages add to Helios, found through entry points.

A package registers a plugin under one of the groups below in its pyproject.toml:

    [project.entry-points."helios.engines"]
    impala = "helios_cloudera.impala:ImpalaEngine"

Settings name plugins (``engine = "impala"``, ``catalog = "atlas"``) and still load
when one is not installed. ``load`` imports a plugin when it is used, ``installed``
reports what is present without importing anything, and ``require_configured``
lets a host check at startup that every plugin its Settings name is installed.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from helios_core.settings import Settings

ENGINES = "helios.engines"
CATALOGS = "helios.catalogs"
STORES = "helios.stores"
TOOLS = "helios.tools"
COMMANDS = "helios.commands"
GROUPS = (ENGINES, CATALOGS, STORES, TOOLS, COMMANDS)

# The package that provides each plugin Helios itself publishes, so a missing one
# says what to install.
PROVIDERS = {
    (ENGINES, "impala"): "helios-cloudera",
    (CATALOGS, "atlas"): "helios-cloudera",
}


class PluginError(Exception):
    """A plugin is missing, registered twice, or fails to load."""


class PluginNotFound(PluginError, LookupError):
    """No installed package registers the plugin."""


class PluginLoadError(PluginError, ImportError):
    """The plugin is registered but importing it failed, usually a missing extra."""


@dataclass(frozen=True)
class PluginInfo:
    group: str
    name: str
    target: str                # "module:attribute", as registered
    distribution: str | None   # the package that registers it
    version: str | None


def _check_group(group: str) -> None:
    if group not in GROUPS:
        raise ValueError(f"unknown plugin group {group!r}; the groups are {', '.join(GROUPS)}")


def _entry_points(group: str) -> tuple[EntryPoint, ...]:
    return tuple(entry_points(group=group))


def _info(entry: EntryPoint) -> PluginInfo:
    dist = entry.dist
    return PluginInfo(entry.group, entry.name, entry.value, dist.name if dist else None, dist.version if dist else None)


def installed(group: str | None = None) -> tuple[PluginInfo, ...]:
    """Every registered plugin, or those of one group, by group and name. Imports none of them."""
    groups = GROUPS if group is None else (group,)
    for name in groups:
        _check_group(name)
    found = {(info.group, info.name, info.target): info for name in groups for info in map(_info, _entry_points(name))}
    return tuple(sorted(found.values(), key=lambda info: (GROUPS.index(info.group), info.name)))


def names(group: str) -> tuple[str, ...]:
    """The names registered in ``group``, sorted."""
    return tuple(sorted({info.name for info in installed(group)}))


def _not_found(group: str, name: str, used_by: str | None = None) -> str:
    present = names(group)
    message = f"no plugin {name!r} in {group}"
    if used_by:
        message += f" (used by {used_by})"
    message += f"; installed: {', '.join(present) if present else 'none'}"
    provider = PROVIDERS.get((group, name))
    if provider:
        message += f"; install {provider}"
    return message


def load(group: str, name: str) -> object:
    """Import and return the plugin ``name`` registered in ``group``."""
    _check_group(group)
    matches = {entry.value: entry for entry in _entry_points(group) if entry.name == name}
    if not matches:
        raise PluginNotFound(_not_found(group, name))
    if len(matches) > 1:
        sources = ", ".join(sorted(f"{info.target} from {info.distribution or 'unknown'}" for info in map(_info, matches.values())))
        raise PluginError(f"plugin {name!r} in {group} is registered more than once: {sources}")
    [entry] = matches.values()
    try:
        return entry.load()
    except Exception as exc:
        source = entry.dist.name if entry.dist else entry.value
        raise PluginLoadError(
            f"plugin {name!r} in {group} (from {source}) failed to load: {type(exc).__name__}: {exc}"
        ) from exc


def missing_configured(settings: Settings) -> tuple[str, ...]:
    """What ``settings`` names but no installed package provides, one line each."""
    wanted = [(ENGINES, connection.engine, f"connection {ref!r}") for ref, connection in settings.connections.items()]
    wanted += [(CATALOGS, catalog.catalog, f"catalog {ref!r}") for ref, catalog in settings.catalogs.items()]
    return tuple(_not_found(group, name, used_by) for group, name, used_by in wanted if name not in names(group))


def require_configured(settings: Settings) -> None:
    """Raise PluginNotFound, listing all of them, if ``settings`` names plugins that are not installed."""
    missing = missing_configured(settings)
    if missing:
        raise PluginNotFound("\n".join(missing))
