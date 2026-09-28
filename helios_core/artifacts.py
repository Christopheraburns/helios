"""Model-aware filesystem storage for generated and published artifacts.

New artifacts live under ``models/<model-id>/<stage>/``. Legacy flat published
artifacts remain readable by model ID during migration.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
STAGES = ("proposed", "published")


class ArtifactStore:
    def __init__(self, root: str | os.PathLike[str] | None = None):
        self.root = Path(
            root
            or os.environ.get("HELIOS_ROOT")
            or os.path.join(
                os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios"
            )
        )
        self.models_dir = self.root / "models"

    def directory(self, model_id: str, stage: str) -> Path:
        _validate_component(model_id, "model_id")
        if stage not in STAGES:
            raise ValueError(f"unknown artifact stage {stage!r}")
        return self.models_dir / model_id / stage

    def path(self, model_id: str, stage: str, filename: str) -> Path:
        _validate_component(filename, "artifact filename")
        return self.directory(model_id, stage) / filename

    def write_json(
        self,
        model_id: str,
        stage: str,
        filename: str,
        document: dict[str, Any],
        *,
        embed_model_id: bool = True,
    ) -> Path:
        _assert_model(document, model_id)
        payload = dict(document)
        if embed_model_id:
            payload["model_id"] = model_id
        path = self.path(model_id, stage, filename)
        _atomic_write(path, json.dumps(payload, indent=2) + "\n")
        return path

    def read_json(
        self, model_id: str, stage: str, filename: str
    ) -> dict[str, Any]:
        path = self.path(model_id, stage, filename)
        with path.open() as handle:
            document = json.load(handle)
        _assert_model(document, model_id)
        return document

    def write_proposal(
        self, model_id: str, run_id: str, proposal: dict[str, Any]
    ) -> Path:
        _validate_component(run_id, "run_id")
        return self.write_json(
            model_id, "proposed", f"{run_id}.proposal.json", proposal
        )

    def write_published_ossie(
        self,
        model_id: str,
        document: dict[str, Any],
        yaml_text: str,
        manifest: dict[str, Any],
    ) -> tuple[Path, Path, Path]:
        _assert_model(document, model_id)
        revision_id = hashlib.sha256(yaml_text.encode()).hexdigest()
        manifest.update({
            "revision_id": revision_id,
            "sha256": revision_id,
            "ossie_version": document.get("version"),
            "size_bytes": len(yaml_text.encode()),
            "path": str(
                (
                    self.revision_directory(model_id, revision_id)
                    / "semantic.ossie.yaml"
                ).relative_to(self.root)
            ),
        })
        self.write_revision(
            model_id,
            document,
            yaml_text,
            manifest,
        )
        published = self.directory(model_id, "published")
        yaml_path = published / "semantic.ossie.yaml"
        json_path = published / "semantic.ossie.json"
        manifest_path = published / "manifest.json"
        _atomic_write(yaml_path, yaml_text)
        self.write_json(
            model_id,
            "published",
            "semantic.ossie.json",
            document,
            embed_model_id=False,
        )
        self.write_json(model_id, "published", "manifest.json", manifest)
        return yaml_path, json_path, manifest_path

    def revision_directory(self, model_id: str, revision_id: str) -> Path:
        _validate_component(model_id, "model_id")
        _validate_component(revision_id, "revision_id")
        return self.models_dir / model_id / "revisions" / revision_id

    def write_revision(
        self,
        model_id: str,
        document: dict[str, Any],
        yaml_text: str,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        _assert_model(document, model_id)
        digest = hashlib.sha256(yaml_text.encode()).hexdigest()
        if manifest.get("revision_id") not in {None, digest}:
            raise ValueError("semantic revision does not match artifact hash")
        directory = self.revision_directory(model_id, digest)
        yaml_path = directory / "semantic.ossie.yaml"
        json_path = directory / "semantic.ossie.json"
        manifest_path = directory / "manifest.json"
        json_text = json.dumps(document, indent=2) + "\n"
        revision = {
            **manifest,
            "model_id": model_id,
            "revision_id": digest,
            "sha256": digest,
            "ossie_version": document.get("version"),
            "size_bytes": len(yaml_text.encode()),
            "json_sha256": hashlib.sha256(json_text.encode()).hexdigest(),
            "path": str(yaml_path.relative_to(self.root)),
        }
        if directory.exists():
            if (
                not yaml_path.exists()
                or yaml_path.read_text() != yaml_text
                or not json_path.exists()
                or json_path.read_text() != json_text
                or not manifest_path.exists()
            ):
                raise RuntimeError(
                    f"immutable semantic revision was modified: {directory}"
                )
            existing = json.loads(manifest_path.read_text())
            if (
                existing.get("revision_id") != digest
                or existing.get("sha256") != digest
                or existing.get("json_sha256")
                != revision["json_sha256"]
            ):
                raise RuntimeError(
                    f"immutable semantic revision was modified: {directory}"
                )
            return existing
        contents = {
            yaml_path: yaml_text,
            json_path: json_text,
            manifest_path: json.dumps(revision, indent=2) + "\n",
        }
        for path, payload in contents.items():
            _atomic_write(path, payload)
        return revision

    def ensure_published_revision(
        self, model_id: str
    ) -> dict[str, Any] | None:
        yaml_path = self.published_ossie_path(model_id, "yaml")
        json_path = self.published_ossie_path(model_id, "json")
        if not yaml_path.exists() or not json_path.exists():
            return None
        yaml_text = yaml_path.read_text()
        with json_path.open() as handle:
            document = json.load(handle)
        _assert_model(document, model_id)
        scoped_manifest = self.path(
            model_id, "published", "manifest.json"
        )
        legacy_manifest = (
            self.models_dir / "published" / f"{model_id}.publish.json"
        )
        manifest_path = (
            scoped_manifest
            if scoped_manifest.exists()
            else legacy_manifest
        )
        manifest = {}
        if manifest_path.exists():
            with manifest_path.open() as handle:
                manifest = json.load(handle)
        return self.write_revision(
            model_id,
            document,
            yaml_text,
            manifest,
        )

    def revision_ossie_path(
        self,
        model_id: str,
        revision_id: str,
        extension: str = "json",
    ) -> Path:
        if extension not in {"yaml", "json"}:
            raise ValueError("Ossie extension must be yaml or json")
        path = self.revision_directory(
            model_id, revision_id
        ) / f"semantic.ossie.{extension}"
        if not path.exists():
            raise FileNotFoundError(
                f"semantic revision {revision_id!r} is unavailable"
            )
        if extension == "yaml":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != revision_id:
                raise RuntimeError("semantic revision hash verification failed")
        else:
            manifest_path = (
                self.revision_directory(model_id, revision_id)
                / "manifest.json"
            )
            if not manifest_path.exists():
                raise RuntimeError("semantic revision manifest is unavailable")
            manifest = json.loads(manifest_path.read_text())
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if (
                manifest.get("revision_id") != revision_id
                or manifest.get("json_sha256") != digest
            ):
                raise RuntimeError("semantic revision hash verification failed")
        return path

    def published_ossie_path(
        self, model_id: str, extension: str = "yaml"
    ) -> Path:
        if extension not in ("yaml", "json"):
            raise ValueError("Ossie extension must be yaml or json")
        scoped = self.path(
            model_id, "published", f"semantic.ossie.{extension}"
        )
        if scoped.exists():
            return scoped
        legacy = self.models_dir / "published" / f"{model_id}.ossie.{extension}"
        if legacy.exists():
            return legacy
        return scoped

    def published_model_ids(self) -> list[str]:
        found = {
            directory.name
            for directory in self.models_dir.iterdir()
            if directory.is_dir()
            and (directory / "published" / "semantic.ossie.yaml").exists()
        } if self.models_dir.is_dir() else set()
        legacy = self.models_dir / "published"
        if legacy.is_dir():
            suffix = ".ossie.yaml"
            found.update(
                path.name[: -len(suffix)]
                for path in legacy.glob(f"*{suffix}")
            )
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


def _atomic_write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(contents)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
