"""Where the ArtifactStore keeps its files: a local directory or object storage, by URL.

    storage_for("/srv/helios")                          a local directory (or file:///srv/helios)
    storage_for("s3://bucket/helios")                   AWS S3, MinIO, Ozone's S3 gateway   [s3]
    storage_for("abfss://container@account.dfs.core.windows.net/helios")   ADLS / Blob   [azure]
    storage_for("gs://bucket/helios")                   Google Cloud Storage                [gcs]
    storage_for("ofs://ozone1/volume/bucket/helios", httpfs_url="http://httpfs:14000")      [ozone]

Every backend offers the same small set of operations on ``/``-separated keys. Only
one of them is conditional, ``create``: write a key only if it does not exist yet,
atomically. Immutable files (published revisions) and the active-version log are
built on it, so a backend needs no compare-and-swap of its own:

* local: a hard link, which fails if the target exists;
* S3: ``If-None-Match: *``;  GCS: ``ifGenerationMatch=0``;  Azure: upload without overwrite;
* Ozone (ofs://): a temporary key renamed onto the target (a rename onto an existing
  file fails). Python reaches ofs:// through Ozone's HttpFS gateway (WebHDFS API); a
  native client would need Java.

Remaining options are passed to the fsspec filesystem (credentials, endpoints).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlsplit

from helios_core._optional import missing_extra

# URL scheme -> (fsspec protocol, extra)
SCHEMES = {
    "s3": ("s3", "s3"), "s3a": ("s3", "s3"),
    "abfs": ("abfs", "azure"), "abfss": ("abfs", "azure"), "az": ("abfs", "azure"),
    "gs": ("gcs", "gcs"), "gcs": ("gcs", "gcs"),
    "ofs": ("webhdfs", "ozone"),
}


class ArtifactStorage(Protocol):
    url: str

    def read_bytes(self, key: str) -> bytes:
        """The key's content; FileNotFoundError when it does not exist."""
        ...

    def write_bytes(self, key: str, data: bytes) -> None:
        """Replace the key's content in one step (readers see the old or the new file)."""
        ...

    def create(self, key: str, data: bytes) -> bool:
        """Write the key only if it does not exist, atomically; False when it already did."""
        ...

    def exists(self, key: str) -> bool: ...

    def list(self, prefix: str) -> list[str]:
        """The names directly under ``prefix`` (files and directories), sorted; [] when none."""
        ...

    def local_path(self, key: str) -> Path | None:
        """The key as a local file path, for a local directory; None for object storage."""
        ...


def _check_key(key: str) -> str:
    parts = PurePosixPath(key).parts
    if key.startswith("/") or any(p in ("..", ".") for p in parts):
        raise ValueError(f"invalid artifact key {key!r}")
    return key


class LocalStorage:
    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)
        self.url = self.root.as_uri() if self.root.is_absolute() else str(self.root)

    def __repr__(self) -> str:
        return f"LocalStorage({str(self.root)!r})"

    def local_path(self, key: str) -> Path:
        return self.root / _check_key(key) if key else self.root

    def read_bytes(self, key: str) -> bytes:
        return self.local_path(key).read_bytes()

    def _temporary(self, path: Path, data: bytes) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        return temporary

    def write_bytes(self, key: str, data: bytes) -> None:
        path = self.local_path(key)
        temporary = self._temporary(path, data)
        try:
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def create(self, key: str, data: bytes) -> bool:
        path = self.local_path(key)
        temporary = self._temporary(path, data)
        try:
            os.link(temporary, path)  # atomic, and fails if the target exists
            return True
        except FileExistsError:
            return False
        finally:
            Path(temporary).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self.local_path(key).exists()

    def list(self, prefix: str) -> list[str]:
        path = self.local_path(prefix.rstrip("/"))
        return sorted(p.name for p in path.iterdir() if not p.name.startswith(".")) if path.is_dir() else []


class FsspecStorage:
    """Object storage through fsspec. ``root`` is the bucket / container path under which keys live."""

    def __init__(self, url: str, fs: Any, root: str):
        self.url, self.fs, self.root = url, fs, root.rstrip("/")

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.url!r})"

    def local_path(self, key: str) -> None:
        return None

    def _path(self, key: str) -> str:
        return f"{self.root}/{_check_key(key)}" if key else self.root

    def read_bytes(self, key: str) -> bytes:
        path = self._path(key)
        self.fs.invalidate_cache(path)
        return self.fs.cat_file(path)

    def write_bytes(self, key: str, data: bytes) -> None:
        path = self._path(key)
        self.fs.pipe_file(path, data)
        self.fs.invalidate_cache(path)

    def _create(self, path: str, data: bytes) -> None:
        """Write ``path`` only if absent; raise when it exists. Each store does this its own way."""
        raise NotImplementedError

    def create(self, key: str, data: bytes) -> bool:
        path = self._path(key)
        try:
            self._create(path, data)
            return True
        except FileExistsError:
            return False
        except Exception:
            # Stores report a failed precondition in different ways (412, 409, their own
            # error types); if the key is there now, someone else created it.
            self.fs.invalidate_cache(path)
            if self.fs.exists(path):
                return False
            raise
        finally:
            self.fs.invalidate_cache(path)

    def exists(self, key: str) -> bool:
        path = self._path(key)
        self.fs.invalidate_cache(path)
        return self.fs.exists(path)

    def list(self, prefix: str) -> list[str]:
        path = self._path(prefix.rstrip("/"))
        self.fs.invalidate_cache(path)
        try:
            entries = self.fs.ls(path, detail=False)
        except FileNotFoundError:
            return []
        own = path.rstrip("/")
        names = {PurePosixPath(e.rstrip("/")).name for e in entries if e.rstrip("/") != own}
        return sorted(n for n in names if n and not n.startswith("."))


class S3Storage(FsspecStorage):
    def _create(self, path: str, data: bytes) -> None:
        self.fs.pipe_file(path, data, mode="create")  # If-None-Match: *


class GcsStorage(FsspecStorage):
    def _create(self, path: str, data: bytes) -> None:
        self.fs.pipe_file(path, data, mode="create")  # ifGenerationMatch=0


class AzureStorage(FsspecStorage):
    def _create(self, path: str, data: bytes) -> None:
        self.fs.pipe_file(path, data, overwrite=False)  # FileExistsError when it exists


class OzoneStorage(FsspecStorage):
    """ofs:// through Ozone's HttpFS gateway, which speaks the WebHDFS API.

    Ozone checks ``overwrite=false`` when a write opens, not when it commits, so racing
    creators can all succeed. ``create`` therefore writes a temporary key and renames it
    onto the target: Ozone's metadata service applies renames one at a time and refuses
    one onto an existing file, so exactly one racing writer wins. HttpFS has no APPEND,
    so every write is a single CREATE carrying the data."""

    def _request(self, method: str, path: str, **params: str) -> Any:
        from urllib.parse import quote

        data = params.pop("body", None)
        response = self.fs.session.request(
            method, self.fs.url + quote("/" + path.lstrip("/"), safe="/="),
            params={**self.fs.pars, **params}, data=data, allow_redirects=False,
            headers={"Content-Type": "application/octet-stream"} if data is not None else None)
        response.raise_for_status()
        return response

    def _put(self, path: str, data: bytes) -> None:
        # The key becomes visible when it is complete, so readers see the old or the new file.
        self._request("PUT", path, op="CREATE", overwrite="true", data="true", body=data)

    def write_bytes(self, key: str, data: bytes) -> None:
        self._put(self._path(key), data)

    def create(self, key: str, data: bytes) -> bool:
        import uuid

        path = self._path(key)
        directory, _, name = path.rpartition("/")
        temporary = f"{directory}/.{name}.{uuid.uuid4().hex}.tmp"
        self._put(temporary, data)
        try:
            renamed = self._request("PUT", temporary, op="RENAME", destination="/" + path.lstrip("/")).json()
            if renamed.get("boolean") is True:
                return True
        except Exception:
            if not self.exists(key):
                raise
        self._request("DELETE", temporary, op="DELETE")
        return False


def _filesystem(protocol: str, extra: str, options: dict[str, Any]) -> Any:
    try:
        if protocol == "gcs":
            # gcsfs's default ("extended") filesystem looks up each bucket's type over a
            # separate gRPC API; Helios needs only object reads, writes and lists.
            from gcsfs.core import GCSFileSystem

            return GCSFileSystem(skip_instance_cache=True, **options)
        import fsspec

        return fsspec.filesystem(protocol, skip_instance_cache=True, **options)
    except (ModuleNotFoundError, ImportError) as exc:
        if isinstance(exc, ModuleNotFoundError):
            raise missing_extra(f"{protocol} artifact storage", extra, exc) from exc
        raise ModuleNotFoundError(str(exc)) from exc


def storage_for(location: str | os.PathLike[str], **options: Any) -> ArtifactStorage:
    """The storage at ``location``: a path or ``file://`` URL, or an object-store URL."""
    text = os.fspath(location)
    parts = urlsplit(text)
    if parts.scheme in ("", "file") or len(parts.scheme) == 1:  # a path (or a Windows drive)
        return LocalStorage(parts.path if parts.scheme == "file" else text)
    if parts.scheme not in SCHEMES:
        raise ValueError(f"unsupported artifact storage {parts.scheme}://; use a path, file://, "
                         f"{', '.join(sorted(s + '://' for s in SCHEMES))}")
    protocol, extra = SCHEMES[parts.scheme]
    if parts.scheme in ("abfs", "abfss", "az"):
        container, _, account = parts.netloc.partition("@")
        if account and "account_name" not in options and "connection_string" not in options:
            options = {"account_name": account.split(".")[0], **options}
        fs = _filesystem(protocol, extra, options)
        return AzureStorage(text, fs, f"{container}{parts.path}")
    if parts.scheme == "ofs":
        httpfs = options.pop("httpfs_url", None)
        if not httpfs:
            raise ValueError("ofs:// storage needs httpfs_url, the Ozone HttpFS gateway (http://host:14000)")
        gateway = urlsplit(httpfs)
        fs = _filesystem(protocol, extra, {"host": gateway.hostname, "port": gateway.port or 14000,
                                           "use_https": gateway.scheme == "https", **options})
        return OzoneStorage(text, fs, parts.path)  # ofs://<service>/<volume>/<bucket>/... -> /<volume>/<bucket>/...
    fs = _filesystem(protocol, extra, options)
    backend = S3Storage if protocol == "s3" else GcsStorage
    return backend(text, fs, f"{parts.netloc}{parts.path}")
