"""PyIceberg SQL catalog for offline tests and CI (SQLite locally, PostgreSQL in CI).

Workbench never uses PyIceberg: that would need direct access to the storage
under the lakehouse. Workbench goes through Impala instead (``lakehouse.impala``).
"""

from pyiceberg.catalog import Catalog
from pyiceberg.catalog.sql import SqlCatalog


def sql_catalog(uri: str, warehouse: str, name: str = "helios_ds") -> Catalog:
    """e.g. ``sql_catalog("sqlite:////tmp/cat.db", "file:///tmp/warehouse")``."""
    return SqlCatalog(name, uri=uri, warehouse=warehouse)
