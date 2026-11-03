"""Artifact storage backends and the active-version log (burndown 4.3).

Every test runs on a local directory and on S3 (an in-process moto server, or the
S3-compatible endpoint HELIOS_TEST_S3 names, with HELIOS_TEST_S3_KEY and
HELIOS_TEST_S3_SECRET), and on each other object store whose variable is set:

    HELIOS_TEST_AZURE=http://127.0.0.1:20000     Azurite (the well-known development account)
    HELIOS_TEST_GCS=http://127.0.0.1:24443       fake-gcs-server
    HELIOS_TEST_OZONE=http://127.0.0.1:14000     Ozone HttpFS, with volume vol1 and bucket bucket1

docker commands for each are in docs/storage.md (4.7); CI runs MinIO (4.6).
"""
import os
import threading
import uuid

import pytest

from helios_core.artifact_storage import LocalStorage, storage_for
from helios_core.artifacts import ActivationConflict, ArtifactError, ArtifactStore
from helios_core.ossie import SemanticModel

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
def storage(request, tmp_path):
    if request.param == "local":
        return LocalStorage(tmp_path / "artifacts")
    if request.param == "s3":
        return _s3(f"run-{uuid.uuid4().hex[:10]}", request.getfixturevalue("s3_endpoint"))
    variable, make = REMOTE[request.param]
    if not os.environ.get(variable):
        pytest.skip(f"set {variable} to test {request.param} storage")
    return make(f"run-{uuid.uuid4().hex[:10]}")


# ---------------------------------------------------------------- the storage contract

def test_keys_are_written_read_and_listed(storage):
    assert not storage.exists("models/a/x.json")
    with pytest.raises(FileNotFoundError):
        storage.read_bytes("models/a/x.json")
    assert storage.list("models") == []
    storage.write_bytes("models/a/x.json", b"one")
    storage.write_bytes("models/a/x.json", b"two")
    storage.write_bytes("models/b/y.json", b"y")
    assert storage.read_bytes("models/a/x.json") == b"two"
    assert storage.exists("models/a/x.json")
    assert storage.list("models") == ["a", "b"]
    assert storage.list("models/a") == ["x.json"]


def test_create_writes_only_when_the_key_is_absent(storage):
    assert storage.create("models/a/once.json", b"first") is True
    assert storage.create("models/a/once.json", b"second") is False
    assert storage.read_bytes("models/a/once.json") == b"first"


def test_exactly_one_of_many_racing_creates_wins(storage):
    results = {}

    def race(i):
        results[i] = storage.create("models/a/race.json", f"writer {i}".encode())

    threads = [threading.Thread(target=race, args=(i,)) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    winners = [i for i, won in results.items() if won]
    assert len(winners) == 1
    assert storage.read_bytes("models/a/race.json") == f"writer {winners[0]}".encode()


def test_keys_cannot_escape_the_root(storage):
    for key in ("../outside", "/etc/passwd", "models/../../x"):
        with pytest.raises(ValueError, match="invalid artifact key"):
            storage.write_bytes(key, b"x")


# ---------------------------------------------------------------- the artifact store on it

def ossie(version):
    return {"version": "0.1.0", "name": f"sales v{version}", "datasets": [], "relationships": [], "metrics": [],
            "custom_extensions": [{"vendor_name": "HELIOS", "data": '{"model_id": "sales"}'}]}


def yaml_text(version):
    return f"version: 0.1.0\nname: sales v{version}\ndatasets: []\nrelationships: []\nmetrics: []\n"


def test_publishing_activates_an_immutable_revision(storage):
    store = ArtifactStore(storage)
    assert store.active_revision("sales") is None and store.published_model_ids() == []

    store.write_published_ossie("sales", ossie(1), yaml_text(1), {"published_by": "alice"}, activated_by="alice")
    first = store.active_revision("sales")
    store.write_published_ossie("sales", ossie(2), yaml_text(2), {}, activated_by="bob")
    second = store.active_revision("sales")

    assert (first.sequence, second.sequence, second.previous_revision_id) == (1, 2, first.revision_id)
    assert [a.activated_by for a in store.activations("sales")] == ["bob", "alice"]
    assert store.read_published_ossie("sales") == yaml_text(2)
    assert store.read_revision("sales", first.revision_id, "yaml") == yaml_text(1)
    assert SemanticModel.load_published("sales", store).name == "sales v2"
    assert store.published_model_ids() == ["sales"]

    # Rolling back is activating an older revision; it is a new entry in the log.
    store.activate("sales", first.revision_id, activated_by="carol")
    assert store.read_published_ossie("sales") == yaml_text(1)
    assert [a.sequence for a in store.activations("sales")] == [3, 2, 1]


def test_a_stale_or_racing_activation_is_a_conflict(storage):
    store = ArtifactStore(storage)
    store.write_published_ossie("sales", ossie(1), yaml_text(1), {})
    revision = store.active_revision("sales").revision_id
    with pytest.raises(ActivationConflict, match="at activation 1, not None"):
        store.activate("sales", revision, expected_sequence=None)
    with pytest.raises(ArtifactError, match="does not exist"):
        store.activate("sales", "0" * 64)

    outcomes = []

    def activate():
        try:
            store.activate("sales", revision, expected_sequence=1)
            outcomes.append("won")
        except ActivationConflict:
            outcomes.append("conflict")

    threads = [threading.Thread(target=activate) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["conflict"] * 7 + ["won"]
    assert store.active_revision("sales").sequence == 2


def test_a_published_revision_cannot_be_rewritten(storage):
    store = ArtifactStore(storage)
    store.write_published_ossie("sales", ossie(1), yaml_text(1), {})
    revision = store.active_revision("sales").revision_id
    storage.write_bytes(f"models/sales/revisions/{revision}/semantic.ossie.json", b'{"tampered": true}\n')
    with pytest.raises(ArtifactError, match="hash verification failed"):
        store.read_published_ossie("sales", "json")
    with pytest.raises(ArtifactError, match="immutable semantic revision was modified"):
        store.write_revision("sales", ossie(1), yaml_text(1), {})


def test_local_paths_exist_only_for_a_local_directory(storage):
    store = ArtifactStore(storage)
    store.write_proposal("sales", "run1", {"tables": []})
    assert store.read_json("sales", "proposed", "run1.proposal.json")["model_id"] == "sales"
    if storage.local_path("") is None:
        assert store.root is None
        with pytest.raises(ArtifactError, match="object storage"):
            store.directory("sales", "proposed")
        assert str(store.location("models/sales/proposed/run1.proposal.json")).startswith(storage.url)
    else:
        assert store.directory("sales", "proposed").is_dir()


def test_storage_urls_pick_the_backend_and_name_missing_extras(tmp_path):
    assert isinstance(storage_for(tmp_path), LocalStorage)
    assert storage_for(f"file://{tmp_path}").root == tmp_path
    with pytest.raises(ValueError, match="unsupported artifact storage ftp://"):
        storage_for("ftp://host/x")
    with pytest.raises(ValueError, match="httpfs_url"):
        storage_for("ofs://ozone1/vol/bucket")
