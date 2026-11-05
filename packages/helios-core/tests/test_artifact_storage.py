"""Helios's artifact storage backends pass the conformance suite (helios_core.conformance).

The suite runs on a local directory and on S3 (an in-process moto server, or the
S3-compatible endpoint HELIOS_TEST_S3 names, with HELIOS_TEST_S3_KEY and
HELIOS_TEST_S3_SECRET), and on each other object store whose variable is set:

    HELIOS_TEST_AZURE=http://127.0.0.1:20000     Azurite (the well-known development account)
    HELIOS_TEST_GCS=http://127.0.0.1:24443       fake-gcs-server
    HELIOS_TEST_OZONE=http://127.0.0.1:14000     Ozone HttpFS, with volume vol1 and bucket bucket1

docker commands for each are in docs/storage.md; CI's storage job runs S3 on a moto container.
"""
import os
import uuid

import pytest

from helios_core.artifact_storage import LocalStorage, storage_for
from helios_core.conformance import ArtifactStorageConformance

AZURITE_KEY = "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="


@pytest.fixture(scope="module")
def s3_endpoint():
    """(endpoint, key, secret): HELIOS_TEST_S3's, else an in-process moto server."""
    if os.environ.get("HELIOS_TEST_S3"):
        yield os.environ["HELIOS_TEST_S3"], os.environ["HELIOS_TEST_S3_KEY"], os.environ["HELIOS_TEST_S3_SECRET"]
        return
    moto = pytest.importorskip("moto.server", reason="needs moto (dev dependency) or HELIOS_TEST_S3")
    server = moto.ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}", "helios", "helios-secret"
    server.stop()


def _s3(prefix, endpoint):
    import s3fs

    url, key, secret = endpoint
    options = {"key": key, "secret": secret, "client_kwargs": {"endpoint_url": url, "region_name": "us-east-1"}}
    fs = s3fs.S3FileSystem(**options)
    if not fs.exists("helios-test"):
        fs.call_s3("create_bucket", Bucket="helios-test")
    return storage_for(f"s3://helios-test/{prefix}", **options)


def _azure(prefix):
    endpoint = os.environ["HELIOS_TEST_AZURE"]
    connection = (f"DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey={AZURITE_KEY};"
                  f"BlobEndpoint={endpoint}/devstoreaccount1;")
    import adlfs

    fs = adlfs.AzureBlobFileSystem(connection_string=connection)
    if not fs.exists("helios-test"):
        fs.mkdir("helios-test")
    return storage_for(f"az://helios-test/{prefix}", connection_string=connection)


def _gcs(prefix):
    endpoint = os.environ["HELIOS_TEST_GCS"]
    from gcsfs.core import GCSFileSystem

    fs = GCSFileSystem(endpoint_url=endpoint, token="anon", project="helios")
    if not fs.exists("helios-test"):
        fs.mkdir("helios-test")
    return storage_for(f"gs://helios-test/{prefix}", endpoint_url=endpoint, token="anon", project="helios")


def _ozone(prefix):
    return storage_for(f"ofs://ozone1/vol1/bucket1/{prefix}", httpfs_url=os.environ["HELIOS_TEST_OZONE"],
                       user="hadoop")


REMOTE = {"azure": ("HELIOS_TEST_AZURE", _azure), "gcs": ("HELIOS_TEST_GCS", _gcs),
          "ozone": ("HELIOS_TEST_OZONE", _ozone)}


@pytest.fixture(params=["local", "s3", *REMOTE])
def backend(request, tmp_path):
    if request.param == "local":
        return LocalStorage(tmp_path / "artifacts")
    if request.param == "s3":
        return _s3(f"run-{uuid.uuid4().hex[:10]}", request.getfixturevalue("s3_endpoint"))
    variable, make = REMOTE[request.param]
    if not os.environ.get(variable):
        pytest.skip(f"set {variable} to test {request.param} storage")
    return make(f"run-{uuid.uuid4().hex[:10]}")


class TestStorageBackends(ArtifactStorageConformance):
    @pytest.fixture
    def storage(self, backend):
        return backend


def test_storage_urls_pick_the_backend_and_name_missing_extras(tmp_path):
    assert isinstance(storage_for(tmp_path), LocalStorage)
    assert storage_for(f"file://{tmp_path}").root == tmp_path
    with pytest.raises(ValueError, match="unsupported artifact storage ftp://"):
        storage_for("ftp://host/x")
    with pytest.raises(ValueError, match="httpfs_url"):
        storage_for("ofs://ozone1/vol/bucket")


def test_settings_put_runs_with_object_storage_artifacts(tmp_path):
    from helios_core.runs import RunStore
    from helios_core.settings import Settings

    local = RunStore.from_settings(Settings(data_dir=tmp_path / "d"))
    assert local.runs_dir == str(tmp_path / "d" / "runs")                # the local default is unchanged
    stored = RunStore.from_settings(Settings(artifact_storage={"location": f"file://{tmp_path / 'shared'}"}))
    stored.write("20261101T120000Z", "harvest", {"tables": []})
    assert (tmp_path / "shared" / "runs" / "20261101T120000Z" / "harvest.json").is_file()
    assert RunStore(tmp_path / "shared" / "runs").run_ids() == ["20261101T120000Z"]   # a runs directory still works
