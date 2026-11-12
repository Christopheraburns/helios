from .base import Engine, QueryResult
from .dialect import SqlDialect, dialect_for, dialect_named


def engine_for(connection) -> Engine:
    """The engine a ``[connections.<name>]`` entry (``Settings.connection(ref)``) names,
    built by its plugin. Raises PluginNotFound when that plugin is not installed."""
    from .. import plugins

    plugin = plugins.load(plugins.ENGINES, connection.engine)
    if not (isinstance(plugin, type) and issubclass(plugin, Engine)):
        raise plugins.PluginError(f"engine plugin {connection.engine!r} is not an Engine subclass: {plugin!r}")
    return plugin.from_connection(connection)


__all__ = ["Engine", "QueryResult", "SqlDialect", "dialect_for", "dialect_named", "engine_for"]
