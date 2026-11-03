"""Model-aware storage for generated and published artifacts, on a local directory or
object storage (``helios_core.artifact_storage``).

Layout, under the storage root:

    models/<model-id>/proposed/<run-id>.proposal.json       proposals
    models/<model-id>/revisions/<sha256>/semantic.ossie.yaml immutable published revisions
                                        /semantic.ossie.json  (written once; never replaced)
                                        /manifest.json
    models/<model-id>/active/<sequence>.json                 the active-version log
    models/<model-id>/published/...                           a copy of the active revision, for
                                                              readers that predate the log

The active revision is the newest entry in the log. Activating creates the next entry
only if it does not exist yet, so two writers cannot both move the pointer from the
same version (ActivationConflict); the log also records every activation. Legacy flat
published artifacts (``models/published/<model-id>.ossie.yaml``) remain readable.

Methods returning a local ``Path`` work on a local directory only; on object storage
use the ``read_*`` methods, which work everywhere.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from helios_core.artifact_storage import ArtifactStorage, LocalStorage, storage_for

if TYPE_CHECKING:
    from helios_core.settings import Settings

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
STAGES = ("proposed", "published")
ANY = object()  # activate(): on top of whatever is active


class ArtifactError(RuntimeError):
    """An artifact is missing, modified or not available on this storage."""


class ActivationConflict(ArtifactError):
    """Another writer moved the active version first."""


@dataclass(frozen=True)
class Activation:
    model_id: str
    revision_id: str
    sequence: int
    activated_at: str
    activated_by: str | None = None
    previous_revision_id: str | None = None


class ArtifactStore:
    def __init__(self, root: str | os.PathLike[str] | ArtifactStorage, **options: Any):
        self.storage: ArtifactStorage = root if hasattr(root, "create") else storage_for(root, **options)
        local = self.storage.local_path("")
        self.root: Path | None = local          # None on object storage
        self.models_dir: Path | None = local / "models" if local is not None else None

    def __repr__(self) -> str:
        return f"ArtifactStore({self.storage!r})"

    @classmethod
    def from_settings(cls, settings: Settings) -> ArtifactStore:
        """``settings.artifact_storage`` when set (an object store), else ``settings.artifacts_dir``."""
        if settings.artifact_storage is not None:
            return cls(storage_for(settings.artifact_storage.location, **settings.artifact_storage.storage_options))
        return cls(settings.artifacts_dir)

    # ------------------------------------------------------------ keys and local paths
    @staticmethod
    def _key(model_id: str, *parts: str) -> str:
        _validate_component(model_id, "model_id")
        return "/".join(("models", model_id, *parts))

    def _stage_key(self, model_id: str, stage: str, filename: str | None = None) -> str:
        if stage not in STAGES:
            raise ValueError(f"unknown artifact stage {stage!r}")
        if filename is None:
            return self._key(model_id, stage)
        _validate_component(filename, "artifact filename")
        return self._key(model_id, stage, filename)

    def _revision_key(self, model_id: str, revision_id: str, filename: str | None = None) -> str:
        _validate_component(revision_id, "revision_id")
        return self._key(model_id, "revisions", revision_id, *([filename] if filename else []))

    def _local(self, key: str) -> Path:
        path = self.storage.local_path(key)
        if path is None:
            raise ArtifactError(f"{self.storage.url} is object storage, not a directory; use the read_* methods")
        return path

    def location(self, key: str) -> Path | str:
        """Where a key lives: a local path, or its URL on object storage."""
        local = self.storage.local_path(key)
        return local if local is not None else f"{self.storage.url.rstrip('/')}/{key}"

    def directory(self, model_id: str, stage: str) -> Path:
        return self._local(self._stage_key(model_id, stage))

    def path(self, model_id: str, stage: str, filename: str) -> Path:
        return self._local(self._stage_key(model_id, stage, filename))

    def revision_directory(self, model_id: str, revision_id: str) -> Path:
        return self._local(self._revision_key(model_id, revision_id))

    # ------------------------------------------------------------ JSON documents
    def write_json(self, model_id: str, stage: str, filename: str, document: dict[str, Any], *,
                   embed_model_id: bool = True) -> Path | str:
        _assert_model(document, model_id)
        payload = dict(document)
        if embed_model_id:
            payload["model_id"] = model_id
        key = self._stage_key(model_id, stage, filename)
        self.storage.write_bytes(key, (json.dumps(payload, indent=2) + "\n").encode())
        return self.location(key)

    def read_json(self, model_id: str, stage: str, filename: str) -> dict[str, Any]:
        document = json.loads(self.storage.read_bytes(self._stage_key(model_id, stage, filename)))
        _assert_model(document, model_id)
        return document

    def write_proposal(self, model_id: str, run_id: str, proposal: dict[str, Any]) -> Path | str:
        _validate_component(run_id, "run_id")
        return self.write_json(model_id, "proposed", f"{run_id}.proposal.json", proposal)

    # ------------------------------------------------------------ immutable revisions
    def write_revision(self, model_id: str, document: dict[str, Any], yaml_text: str,
                       manifest: dict[str, Any]) -> dict[str, Any]:
        """Store a revision once, named by its YAML's SHA-256. Storing the same revision
        again returns its manifest; different content under that name is an error."""
        _assert_model(document, model_id)
        digest = hashlib.sha256(yaml_text.encode()).hexdigest()
        if manifest.get("revision_id") not in {None, digest}:
            raise ValueError("semantic revision does not match artifact hash")
        json_text = json.dumps(document, indent=2) + "\n"
        yaml_key = self._revision_key(model_id, digest, "semantic.ossie.yaml")
        revision = {
            **manifest,
            "model_id": model_id,
            "revision_id": digest,
            "sha256": digest,
            "ossie_version": document.get("version"),
            "size_bytes": len(yaml_text.encode()),
            "json_sha256": hashlib.sha256(json_text.encode()).hexdigest(),
            "path": yaml_key,
        }
        files = {
            yaml_key: yaml_text.encode(),
            self._revision_key(model_id, digest, "semantic.ossie.json"): json_text.encode(),
        }
        for key, data in files.items():
            if not self.storage.create(key, data) and self.storage.read_bytes(key) != data:
                raise ArtifactError(f"immutable semantic revision was modified: {key}")
        manifest_key = self._revision_key(model_id, digest, "manifest.json")
        if self.storage.create(manifest_key, (json.dumps(revision, indent=2) + "\n").encode()):
            return revision
        existing = json.loads(self.storage.read_bytes(manifest_key))
        if (existing.get("revision_id"), existing.get("sha256"), existing.get("json_sha256")) != (
                digest, digest, revision["json_sha256"]):
            raise ArtifactError(f"immutable semantic revision was modified: {manifest_key}")
        return existing

    def read_revision(self, model_id: str, revision_id: str, extension: str = "json") -> str:
        """A revision's OSSIE document, checked against its hash."""
        return self._verified_revision(model_id, revision_id, extension).decode()

    def _verified_revision(self, model_id: str, revision_id: str, extension: str) -> bytes:
        if extension not in {"yaml", "json"}:
            raise ValueError("Ossie extension must be yaml or json")
        try:
            data = self.storage.read_bytes(self._revision_key(model_id, revision_id, f"semantic.ossie.{extension}"))
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"semantic revision {revision_id!r} is unavailable") from exc
        if extension == "yaml":
            expected = revision_id
        else:
            try:
                manifest = json.loads(self.storage.read_bytes(self._revision_key(model_id, revision_id, "manifest.json")))
            except FileNotFoundError as exc:
                raise ArtifactError("semantic revision manifest is unavailable") from exc
            if manifest.get("revision_id") != revision_id:
                raise ArtifactError("semantic revision hash verification failed")
            expected = manifest.get("json_sha256")
        if hashlib.sha256(data).hexdigest() != expected:
            raise ArtifactError("semantic revision hash verification failed")
        return data

    def revision_ossie_path(self, model_id: str, revision_id: str, extension: str = "json") -> Path:
        self._verified_revision(model_id, revision_id, extension)
        return self._local(self._revision_key(model_id, revision_id, f"semantic.ossie.{extension}"))

    # ------------------------------------------------------------ the active version
    def _activation_key(self, model_id: str, sequence: int) -> str:
        return self._key(model_id, "active", f"{sequence:012d}.json")

    def activations(self, model_id: str) -> list[Activation]:
        """Every activation of the model, newest first."""
        names = [n for n in self.storage.list(self._key(model_id, "active")) if re.fullmatch(r"\d{12}\.json", n)]
        return [Activation(**json.loads(self.storage.read_bytes(self._key(model_id, "active", n))))
                for n in sorted(names, reverse=True)]

    def active_revision(self, model_id: str) -> Activation | None:
        names = [n for n in self.storage.list(self._key(model_id, "active")) if re.fullmatch(r"\d{12}\.json", n)]
        if not names:
            return None
        return Activation(**json.loads(self.storage.read_bytes(self._key(model_id, "active", max(names)))))

    def activate(self, model_id: str, revision_id: str, *, expected_sequence: Any = ANY,
                 activated_by: str | None = None) -> Activation:
        """Make ``revision_id`` the model's active revision. ``expected_sequence`` is the
        sequence the caller last saw (None: nothing active); when the log has moved since,
        or another writer activates at the same moment, ActivationConflict."""
        if not self.storage.exists(self._revision_key(model_id, revision_id, "manifest.json")):
            raise ArtifactError(f"semantic revision {revision_id!r} of model {model_id!r} does not exist")
        current = self.active_revision(model_id)
        seen = current.sequence if current else None
        if expected_sequence is not ANY and expected_sequence != seen:
            raise ActivationConflict(f"model {model_id!r} is at activation {seen}, not {expected_sequence}")
        activation = Activation(
            model_id=model_id, revision_id=revision_id, sequence=(seen or 0) + 1,
            activated_at=datetime.now(timezone.utc).isoformat(), activated_by=activated_by,
            previous_revision_id=current.revision_id if current else None)
        if not self.storage.create(self._activation_key(model_id, activation.sequence),
                                   (json.dumps(asdict(activation), indent=2) + "\n").encode()):
            raise ActivationConflict(f"another writer activated model {model_id!r} at {activation.sequence} first")
        return activation

    # ------------------------------------------------------------ publishing
    def write_published_ossie(self, model_id: str, document: dict[str, Any], yaml_text: str,
                              manifest: dict[str, Any], *, activated_by: str | None = None,
                              ) -> tuple[Path | str, Path | str, Path | str]:
        """Store the revision, make it active, and refresh the ``published/`` copy.
        Publishing means "make this current", so a concurrent activation is retried on top."""
        _assert_model(document, model_id)
        revision = self.write_revision(model_id, document, yaml_text, dict(manifest))
        manifest.update({k: revision[k] for k in ("revision_id", "sha256", "ossie_version", "size_bytes", "path")})
        for attempt in range(5):
            try:
                self.activate(model_id, revision["revision_id"], activated_by=activated_by)
                break
            except ActivationConflict:
                if attempt == 4:
                    raise
        yaml_key = self._stage_key(model_id, "published", "semantic.ossie.yaml")
        self.storage.write_bytes(yaml_key, yaml_text.encode())
        json_location = self.write_json(model_id, "published", "semantic.ossie.json", document, embed_model_id=False)
        manifest_location = self.write_json(model_id, "published", "manifest.json", manifest)
        return self.location(yaml_key), json_location, manifest_location

    def ensure_published_revision(self, model_id: str) -> dict[str, Any] | None:
        """The revision for the published copy, stored if it was not (models published before revisions)."""
        try:
            yaml_text = self._published_copy(model_id, "yaml").decode()
            document = json.loads(self._published_copy(model_id, "json"))
        except FileNotFoundError:
            return None
        _assert_model(document, model_id)
        manifest: dict[str, Any] = {}
        for key in (self._stage_key(model_id, "published", "manifest.json"),
                    f"models/published/{model_id}.publish.json"):
            if self.storage.exists(key):
                manifest = json.loads(self.storage.read_bytes(key))
                break
        return self.write_revision(model_id, document, yaml_text, manifest)

    def _published_copy(self, model_id: str, extension: str) -> bytes:
        """The ``published/`` copy, else the legacy flat file; FileNotFoundError when neither exists."""
        for key in (self._stage_key(model_id, "published", f"semantic.ossie.{extension}"),
                    f"models/published/{model_id}.ossie.{extension}"):
            if self.storage.exists(key):
                return self.storage.read_bytes(key)
        raise FileNotFoundError(f"model {model_id!r} has no published semantic model")

    def read_published_ossie(self, model_id: str, extension: str = "yaml") -> str:
        """The model's published OSSIE document: the active revision, verified, else the
        published copy of a model published before the activation log."""
        if extension not in ("yaml", "json"):
            raise ValueError("Ossie extension must be yaml or json")
        active = self.active_revision(model_id)
        if active is not None:
            return self.read_revision(model_id, active.revision_id, extension)
        return self._published_copy(model_id, extension).decode()

    def published_ossie_path(self, model_id: str, extension: str = "yaml") -> Path:
        if extension not in ("yaml", "json"):
            raise ValueError("Ossie extension must be yaml or json")
        scoped = self._stage_key(model_id, "published", f"semantic.ossie.{extension}")
        if self.storage.exists(scoped):
            return self._local(scoped)
        legacy = f"models/published/{model_id}.ossie.{extension}"
        if self.storage.exists(legacy):
            return self._local(legacy)
        return self._local(scoped)

    def published_model_ids(self) -> list[str]:
        found = {
            name for name in self.storage.list("models")
            if name != "published" and (
                self.storage.list(f"models/{name}/active")
                or self.storage.exists(f"models/{name}/published/semantic.ossie.yaml"))
        }
        suffix = ".ossie.yaml"
        found.update(n[: -len(suffix)] for n in self.storage.list("models/published") if n.endswith(suffix))
        return sorted(found)


def model_id_for_run(
    *documents: dict[str, Any],
    explicit: str | None = None,
    legacy_default: str | None = None,
) -> str:
    """Resolve one model identity and reject conflicting run artifacts."""
    candidates = [
        value
        for value in [
            explicit,
            *(document.get("model_id") for document in documents),
        ]
        if value
    ]
    if not candidates:
        if not legacy_default:
            raise ValueError("model_id is required")
        candidates = [legacy_default]
    model_id = candidates[0]
    _validate_component(model_id, "model_id")
    if any(candidate != model_id for candidate in candidates[1:]):
        raise ValueError(f"conflicting model IDs: {candidates}")
    return model_id


def _assert_model(document: dict[str, Any], model_id: str) -> None:
    _validate_component(model_id, "model_id")
    existing = document.get("model_id")
    if not existing:
        for extension in document.get("custom_extensions") or []:
            if extension.get("vendor_name") != "HELIOS":
                continue
            try:
                existing = json.loads(extension.get("data") or "{}").get(
                    "model_id"
                )
            except json.JSONDecodeError:
                pass
            if existing:
                break
    if existing and existing != model_id:
        raise ValueError(
            f"artifact belongs to model {existing!r}, not {model_id!r}"
        )


def _validate_component(value: str, field_name: str) -> None:
    if not value or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"invalid {field_name}: {value!r}")


__all__ = ["ANY", "Activation", "ActivationConflict", "ArtifactError", "ArtifactStore", "LocalStorage", "model_id_for_run"]
