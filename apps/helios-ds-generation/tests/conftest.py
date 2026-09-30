import io
import json
import os
import uuid
from pathlib import Path

import duckdb
import pytest

from helios_ds.config import DatasetConfig
from helios_ds.lakehouse import DUCKDB, IcebergCatalogSink, SqlLakehouseSink, sql_catalog
from helios_ds.object_store import LocalObjectStore
from helios_ds.templates import TemplateRegistry
from helios_ds.tpcds import DuckDbTpcds

TINY = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"

# Small enough to generate in about a second; every scenario has eligible rows.
SMALL_SCALE_FACTOR = 0.01


@pytest.fixture
def tiny_config_dict() -> dict:
    return json.loads((TINY / "config.json").read_text())


@pytest.fixture
def tiny_config(tiny_config_dict) -> DatasetConfig:
    return DatasetConfig.model_validate(tiny_config_dict)


@pytest.fixture
def tiny_store_returns() -> list:
    return json.loads((TINY / "store_returns.json").read_text())


@pytest.fixture(scope="session")
def small_tpcds_path(tmp_path_factory) -> str:
    path = str(tmp_path_factory.mktemp("tpcds") / "small.duckdb")
    DuckDbTpcds.generate(SMALL_SCALE_FACTOR, path).con.close()
    return path


@pytest.fixture
def small_repo(small_tpcds_path) -> DuckDbTpcds:
    return DuckDbTpcds.open(small_tpcds_path)


@pytest.fixture
def small_scale_factor() -> float:
    return SMALL_SCALE_FACTOR


@pytest.fixture
def make_shuffled_repo(small_tpcds_path):
    """Copies of small-TPC-DS tables with the same rows in a different physical order."""

    def _make(tables, seed: float = 0.5) -> DuckDbTpcds:
        con = duckdb.connect()
        con.sql(f"ATTACH '{small_tpcds_path}' AS src (READ_ONLY)")
        con.sql(f"SELECT setseed({seed})")
        for table in sorted(set(tables)):
            con.sql(f"CREATE TABLE {table} AS SELECT * FROM src.{table} ORDER BY random()")
        return DuckDbTpcds(con)

    return _make


@pytest.fixture(scope="session")
def templates() -> TemplateRegistry:
    return TemplateRegistry.load()


@pytest.fixture
def make_catalog(tmp_path):
    """Isolated Iceberg SQL catalogs. HELIOS_DS_TEST_CATALOG_URI switches the
    backing database (CI runs the suite against PostgreSQL too); a unique
    catalog name keeps tests apart within one database."""

    def _make(label: str = "c"):
        uri = os.environ.get("HELIOS_DS_TEST_CATALOG_URI") or f"sqlite:///{tmp_path}/{label}.db"
        warehouse = f"file://{tmp_path}/{label}-warehouse"
        return sql_catalog(uri, warehouse, name=f"test_{uuid.uuid4().hex}")

    return _make


@pytest.fixture(params=["iceberg-catalog", "sql"])
def make_sink(request, make_catalog, tmp_path):
    """Fresh lakehouse sinks of both kinds: the PyIceberg catalog sink (offline/CI)
    and the SQL sink the Impala path uses, run here on DuckDB as a stand-in."""

    def _make(label: str = "c"):
        if request.param == "iceberg-catalog":
            return IcebergCatalogSink(make_catalog(label))
        path = str(tmp_path / f"{label}-lakehouse.duckdb")
        return SqlLakehouseSink(lambda: duckdb.connect(path), DUCKDB)

    return _make


@pytest.fixture
def make_env(make_sink, tmp_path):
    """A fresh (sink, object store) pair: one clean generation environment."""

    def _make(label: str):
        sink = make_sink(label)
        sink.ensure_tables()
        return sink, LocalObjectStore(str(tmp_path / f"{label}-objects"))

    return _make


class FakeS3Client:
    """Just enough of the boto3 S3 client that a Workbench data connection returns."""

    def __init__(self, deny=False):
        self.objects = {}
        self.deny = deny

    def get_object(self, Bucket, Key):
        from botocore.exceptions import ClientError

        if self.deny:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def put_object(self, Bucket, Key, Body):
        self.objects[(Bucket, Key)] = Body

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=2):
        # Small pages (S3 returns up to 1,000) so tests exercise paging.
        keys = sorted(k for b, k in self.objects if b == Bucket and k.startswith(Prefix))
        return {
            "Contents": [{"Key": k} for k in keys[:MaxKeys]],
            "IsTruncated": len(keys) > MaxKeys,
        }

    def delete_objects(self, Bucket, Delete):
        for obj in Delete["Objects"]:
            self.objects.pop((Bucket, obj["Key"]), None)
        return {}


@pytest.fixture
def fake_s3():
    """The FakeS3Client class (call it to get a client)."""
    return FakeS3Client
