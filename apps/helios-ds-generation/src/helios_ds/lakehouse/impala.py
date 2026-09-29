"""Impala connections for the Workbench lakehouse path.

Uses the same settings as the rest of Helios (``helios_core.config``):
IMPALA_HOST (hostname or CDW JDBC URL), WORKLOAD_USER, WORKLOAD_PASSWORD, and
optionally IMPALA_PORT / IMPALA_HTTP_PATH. Connects to the Cloudera Data
Warehouse Impala Virtual Warehouse over HTTPS with LDAP, as ImpalaEngine does.
"""

from typing import Any, Callable

from .sink import SqlLakehouseSink
from .tables import IMPALA


def impala_connector(database: str = "default") -> Callable[[], Any]:
    """A zero-argument factory returning a new impyla DB-API connection."""
    from helios_core.config import impala_config

    cfg = impala_config()
    if cfg is None:
        raise ValueError(
            "Impala is not configured: set IMPALA_HOST, WORKLOAD_USER, WORKLOAD_PASSWORD"
        )

    def connect() -> Any:
        from impala.dbapi import connect as impyla_connect

        return impyla_connect(
            host=cfg.host,
            port=cfg.port,
            database=database,
            user=cfg.user,
            password=cfg.password,
            auth_mechanism="LDAP",
            use_ssl=True,
            use_http_transport=True,
            http_path=cfg.http_path,
        )

    return connect


def impala_sink() -> SqlLakehouseSink:
    return SqlLakehouseSink(impala_connector(), IMPALA)
