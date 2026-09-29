"""Build lakehouse, TPC-DS and object-store backends from short URIs.

  Lakehouse:     impala  (Workbench: the tpcds lakehouse, via the Impala Virtual Warehouse)
                 sql:<sqlalchemy-uri>  (offline: PyIceberg SQL catalog; needs a warehouse)
  TPC-DS:        impala[:database] | duckdb:/path/tpcds.duckdb | duckdb-generate:<scale_factor>
  Object store:  s3a://bucket/prefix (or s3://) via a Workbench data connection | file:/path

Environment defaults: HELIOS_DS_LAKEHOUSE, HELIOS_DS_WAREHOUSE, HELIOS_DS_TPCDS,
HELIOS_DS_OBJECT_STORE. ``impala`` uses the Helios Impala settings (IMPALA_HOST,
WORKLOAD_USER, WORKLOAD_PASSWORD). ``s3a://`` uses the Workbench data connection
named by HELIOS_DS_S3_CONNECTION (default "S3 Object Store").
"""

import os
from typing import Optional

from .lakehouse import IcebergCatalogSink, LakehouseSink, impala_connector, impala_sink, sql_catalog
from .object_store import (
    DEFAULT_S3_CONNECTION,
    LocalObjectStore,
    ObjectStore,
    S3ObjectStore,
    s3_client_from_connection,
)
from .tpcds import DuckDbTpcds, ImpalaTpcds, TpcdsRepository


def _require(value: Optional[str], name: str) -> str:
    if not value:
        raise ValueError(f"{name} is not set")
    return value


def lakehouse_from_uri(uri: Optional[str] = None, warehouse: Optional[str] = None) -> LakehouseSink:
    uri = _require(uri or os.environ.get("HELIOS_DS_LAKEHOUSE"), "HELIOS_DS_LAKEHOUSE")
    if uri == "impala":
        return impala_sink()
    if uri.startswith("sql:"):
        warehouse = _require(
            warehouse or os.environ.get("HELIOS_DS_WAREHOUSE"), "HELIOS_DS_WAREHOUSE"
        )
        return IcebergCatalogSink(sql_catalog(uri[len("sql:") :], warehouse))
    raise ValueError(f"unsupported lakehouse URI {uri!r}; use impala or sql:<uri>")


def tpcds_from_uri(uri: Optional[str] = None) -> TpcdsRepository:
    uri = _require(uri or os.environ.get("HELIOS_DS_TPCDS"), "HELIOS_DS_TPCDS")
    if uri == "impala" or uri.startswith("impala:"):
        return ImpalaTpcds(impala_connector(uri[len("impala:") :] or "tpcds"))
    if uri.startswith("duckdb:"):
        return DuckDbTpcds.open(uri[len("duckdb:") :])
    if uri.startswith("duckdb-generate:"):
        return DuckDbTpcds.generate(float(uri[len("duckdb-generate:") :]))
    raise ValueError(f"unsupported TPC-DS URI {uri!r}")


def object_store_from_uri(uri: Optional[str] = None) -> ObjectStore:
    uri = _require(uri or os.environ.get("HELIOS_DS_OBJECT_STORE"), "HELIOS_DS_OBJECT_STORE")
    if uri.startswith("file:"):
        return LocalObjectStore(uri[len("file:") :])
    for scheme in ("s3a://", "s3://"):
        if uri.startswith(scheme):
            bucket, _, prefix = uri[len(scheme) :].partition("/")
            connection = os.environ.get("HELIOS_DS_S3_CONNECTION") or DEFAULT_S3_CONNECTION
            return S3ObjectStore(s3_client_from_connection(connection), bucket, prefix)
    raise ValueError(f"unsupported object store URI {uri!r}; use s3a://bucket/prefix or file:/path")
