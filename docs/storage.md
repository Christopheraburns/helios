# Storage: the model registry and the artifact store

Helios keeps two kinds of state, and you choose a backend for each:

| | What it holds | Configured by | Default |
|---|---|---|---|
| **Model registry** | Data sources, models (status, who created them) and the metadata of each published semantic revision | `registry_url`, any SQLAlchemy URL | `sqlite:///<data_dir>/state/registry.db` |
| **Artifact store** | Proposals, published semantic model files (immutable revisions), the active-version log, and discovery runs | `[artifact_storage]`, a location URL plus options | the local directory `<data_dir>` (`models/`, `runs/`) |

`data_dir` defaults to the user's data directory (`platformdirs`, e.g. `~/.local/share/helios` on Linux).
Neither store holds identities or permissions: which models a caller may see is the host's `ModelVisibility`, and queries run as the caller under the connection's identity mode.

## Choosing backends

| Deployment | Registry | Artifacts |
|---|---|---|
| One person, one machine (a notebook, a laptop, a Workbench session) | the SQLite default | the local directory default |
| Several processes on **one** host | SQLite works for light use; Postgres if writers contend | the local directory |
| Several hosts, or anything that must survive a host failing | **Postgres**, provider-managed | **object storage**: S3, ADLS / Blob, GCS or Ozone |

Tested backends, which pass the [conformance suite](#testing-a-backend) in CI or locally:

- **Registry:** SQLite and PostgreSQL (17). Other databases SQLAlchemy supports (MySQL, SQL Server, Oracle, Snowflake…) are accepted by URL with their driver installed, but are not tested; run the conformance suite against one before relying on it.
- **Artifacts:** a local directory; S3 (CI tests it on moto); ADLS Gen2 / Blob storage (Azurite); Google Cloud Storage (fake-gcs-server); Ozone 2.0 through HttpFS. Real AWS, Azure and Google services and MinIO speak the same APIs but are not run in CI.

A local directory is for one host only. A directory shared between hosts (NFS, SMB) is **not supported**: publishing relies on hard links being atomic, which network filesystems do not all guarantee, and it is not tested.

Registry and storage plugins from other packages: the conformance suite can test them (below), but Helios cannot select one by URL yet. `registry_url` and `[artifact_storage]` reach only the backends above.

## Configuring

Settings resolve in this order: arguments to `Settings(...)`, `HELIOS_*` environment variables (sections nest with `__`), platform variables (e.g. `WorkbenchSettings`), the config file (`HELIOS_CONFIG`, else `./helios.toml`, else `[tool.helios]` in `./pyproject.toml`), then defaults.

### The registry

```toml
# helios.toml
registry_url = "postgresql+psycopg://helios@db.internal:5432/helios"
```

or `HELIOS_REGISTRY_URL=postgresql+psycopg://…`. Postgres needs `pip install 'helios-core[postgres]'` (psycopg 3); another database needs its SQLAlchemy driver.

Tables are prefixed `helios_` (`helios_models`, `helios_data_sources`, `helios_model_data_sources`, `helios_semantic_revisions`, and the migration table `helios_registry_version`), so the registry can share a database with other applications.

The schema is managed by Alembic migrations that ship in helios-core:

```sh
python -m helios_core.registry current    # the schema version the database is at
python -m helios_core.registry upgrade    # bring it to this helios-core's version
```

The local SQLite default is created and upgraded automatically. **A shared database is never upgraded automatically**: until an operator runs `upgrade`, every registry call raises `RegistryNotReady`, naming the command. Run it once, with credentials that can create tables, before you roll out a new helios-core version.

### The artifact store

`location` picks the backend; every other key is passed to the storage's filesystem library (s3fs, adlfs, gcsfs, or fsspec's WebHDFS for Ozone).

**S3** (and S3-compatible stores: MinIO, Ozone's S3 gateway, Ceph RGW) needs `[s3]`:

```toml
[artifact_storage]
location = "s3://my-bucket/helios"
# Leave out key / secret to use the AWS credential chain (instance or task role,
# web identity, AWS_PROFILE). For an S3-compatible endpoint:
# endpoint_url = "https://minio.internal:9000"
# client_kwargs = { region_name = "us-east-1" }
```

The store must support conditional writes (`If-None-Match: *` on PUT). AWS S3 does; for another S3-compatible store, run the conformance suite against it first.

**ADLS Gen2 / Blob storage** needs `[azure]`:

```toml
[artifact_storage]
location = "abfss://container@myaccount.dfs.core.windows.net/helios"   # also abfs://, az://
# account_name comes from the URL. Credentials: account_key, sas_token,
# connection_string, or a service principal (tenant_id, client_id, client_secret),
# as adlfs accepts them.
```

**Google Cloud Storage** needs `[gcs]`:

```toml
[artifact_storage]
location = "gs://my-bucket/helios"
# Leave out token to use Application Default Credentials (workload identity, a VM's
# service account, GOOGLE_APPLICATION_CREDENTIALS); project = "..." if needed.
```

**Ozone** (`ofs://`) needs `[ozone]` and goes through Ozone's HttpFS gateway (the WebHDFS API), since a native ofs:// client needs Java:

```toml
[artifact_storage]
location = "ofs://ozone1/volume/bucket/helios"   # ofs://<service>/<volume>/<bucket>/<path>
httpfs_url = "https://httpfs.internal:14000"
user = "helios"                                    # the user HttpFS acts as (simple authentication)
```

The bucket should use the `FILE_SYSTEM_OPTIMIZED` layout. Kerberos (SPNEGO) to HttpFS is not tested. Ozone's S3 gateway can be reached through `s3://` as well, but it is not tested; run the conformance suite against it before relying on it.

The same settings as environment variables:

```sh
HELIOS_ARTIFACT_STORAGE__LOCATION=s3://my-bucket/helios
HELIOS_ARTIFACT_STORAGE__ENDPOINT_URL=https://minio.internal:9000
HELIOS_ARTIFACT_STORAGE__CLIENT_KWARGS__REGION_NAME=us-east-1     # nested options nest with __
```

When `[artifact_storage]` is set, discovery runs are written to the same store under `runs/`; otherwise they stay in `runs_dir`.

## Secrets

- `registry_url` is a secret: it often carries a password. It is never printed by `Settings` or by `SqlRegistry`'s repr (the password shows as `***`).
- Storage options whose names are, or end in, `password`, `token`, `secret`, `api_key`, `account_key`, `connection_string` or `credential` (so `client_secret`, `sas_token` too) are kept as `SecretStr` and print as `**********`. Other options print as they are. `key`, an S3 access key **ID**, is not treated as a secret.
- Prefer the platform's identity over keys in a file: an AWS role, an Azure managed identity or service principal, Google workload identity. Leave the credential options out, and the filesystem library uses its own credential chain.
- When you must use keys, put them in environment variables from your secret manager (`HELIOS_REGISTRY_URL`, `HELIOS_ARTIFACT_STORAGE__SECRET`, …), not in a `helios.toml` checked into a repository.
- Grant only what Helios needs: on the bucket or container prefix, read, write, list and conditional create. Helios never deletes artifacts; the only keys it removes are its own temporary files while creating one (local directory and Ozone). In the registry database: read and write on the `helios_` tables, plus DDL for whoever runs `upgrade`.

## High availability

Helios keeps no state in its processes: everything is in the registry and the artifact store, so any number of processes, on any number of hosts, can serve the same models once both stores are shared and highly available. Helios does not replicate or fail over either store itself; it relies on the provider for that.

**Registry.** Use a provider-managed Postgres with automatic failover (Amazon RDS or Aurora, Azure Database for PostgreSQL, Cloud SQL, or your platform team's equivalent), and the provider's backups and point-in-time recovery.

- A failover or an idle timeout drops the pool's connections. The registry tests each connection before use and replaces a dead one, so the next call succeeds once the database is reachable again. Calls made *during* a failover fail with a SQLAlchemy `OperationalError`; retrying them is the host's choice.
- Concurrent writers are safe: the conformance suite saves models from 20 threads, and the same revision from 10 racing threads, on Postgres.
- Point `registry_url` at the provider's failover endpoint (the cluster or writer endpoint), not at one instance.

**Artifacts.** Use object storage. S3, Azure Blob storage, GCS and Ozone all give strong read-after-write consistency, which the active-version log depends on.

- Published revisions are immutable: each file is written once, by a create that fails if the key exists, and named by its content hash, so a retry cannot change a published model and reads verify the hash.
- The active version is a log (`models/<id>/active/<sequence>.json`) whose next entry is created only if absent. Processes publishing the same model at once each get their own activation, and the newest is served: a publisher that loses the race retries on top of the winner, up to five times, then raises `ActivationConflict` (the suite tests four at once). `activate(..., expected_sequence=)` lets a host refuse to activate on top of a version it has not seen (`ActivationConflict`). Rolling back is activating an older revision, and the log records who activated what.
- Durability and region failover are the store's: turn on versioning or soft delete if you want protection against deletes, and replication if you need another region.
- **Ozone:** HttpFS is a single gateway. Run more than one behind a load balancer if it must not be a single point of failure; each request stands alone, so any gateway can serve it.
- A local directory is as available as its host.

### Key layout

Under the artifact store's location:

```
models/<model-id>/proposed/<run-id>.proposal.json
models/<model-id>/revisions/<sha256>/semantic.ossie.yaml | semantic.ossie.json | manifest.json   (immutable)
models/<model-id>/active/<sequence>.json         the active-version log
models/<model-id>/published/…                    a copy of the active revision, for older readers
runs/<run-id>/{harvest,profile,propose,review}.json
```

A local directory uses the same layout, so moving to object storage is a copy of the directory (`aws s3 sync <data_dir>/models s3://bucket/helios/models`, and `runs/` likewise).

## Testing a backend

`helios_core.conformance` holds the tests every backend must pass (`pip install 'helios-core[testing]'`). Subclass a suite and provide a fixture that returns an empty backend:

```python
import pytest
from helios_core.conformance import ArtifactStorageConformance, RegistryConformance

class TestMyRegistry(RegistryConformance):
    @pytest.fixture
    def registry(self):
        return MyRegistry(...)          # a ModelRegistry with no records

class TestMyStorage(ArtifactStorageConformance):
    @pytest.fixture
    def storage(self, tmp_path):
        return MyStorage(...)           # an ArtifactStorage with no keys
```

To test a real deployment's stores, have the fixture return a backend on an empty database and a fresh prefix: the tests write records and keys of their own, so never point them at a database or prefix that holds data.

Helios's own tests run the suites on every backend whose variable is set. CI sets Postgres and S3 (the `storage` job; moto stands in for S3 because MinIO no longer publishes images). Locally:

```sh
# Postgres
docker run -d --name helios-pg -e POSTGRES_PASSWORD=helios -p 127.0.0.1:25432:5432 postgres:17
export HELIOS_TEST_POSTGRES=postgresql+psycopg://postgres:helios@127.0.0.1:25432/postgres

# S3: without a variable, an in-process moto server runs anyway. For another endpoint:
docker run -d --name helios-moto -p 127.0.0.1:25555:5000 motoserver/moto:5.2.5
export HELIOS_TEST_S3=http://127.0.0.1:25555 HELIOS_TEST_S3_KEY=helios HELIOS_TEST_S3_SECRET=helios-secret

# Azure (Azurite, the well-known development account)
docker run -d --name helios-azurite -p 127.0.0.1:20000:10000 mcr.microsoft.com/azure-storage/azurite \
  azurite-blob --blobHost 0.0.0.0 --skipApiVersionCheck
export HELIOS_TEST_AZURE=http://127.0.0.1:20000

# GCS
docker run -d --name helios-fakegcs -p 127.0.0.1:24443:4443 fsouza/fake-gcs-server \
  -scheme http -port 4443 -public-host 127.0.0.1:24443
export HELIOS_TEST_GCS=http://127.0.0.1:24443

# Ozone: one node plus HttpFS on 127.0.0.1:14000, then volume vol1 and bucket bucket1.
docker compose -p helios-ozone -f packages/helios-core/tests/ozone/docker-compose.yaml up -d
# The OM can take several minutes to accept clients, and until then `ozone sh` may
# block indefinitely, so each attempt is cut off after 20 seconds:
until perl -e 'alarm 20; exec @ARGV' docker exec helios-ozone-om-1 ozone sh volume create /vol1; do sleep 5; done
until perl -e 'alarm 20; exec @ARGV' docker exec helios-ozone-om-1 \
  ozone sh bucket create /vol1/bucket1 --layout FILE_SYSTEM_OPTIMIZED; do sleep 5; done
export HELIOS_TEST_OZONE=http://127.0.0.1:14000

uv run pytest packages/helios-core/tests/test_registry.py packages/helios-core/tests/test_registry_sql.py \
  packages/helios-core/tests/test_artifact_storage.py packages/helios-core/tests/test_conformance.py
```

Clean up with `docker rm -f helios-pg helios-moto helios-azurite helios-fakegcs` and `docker compose -p helios-ozone -f packages/helios-core/tests/ozone/docker-compose.yaml down -v`.
