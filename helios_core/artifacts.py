"""Model-aware filesystem storage for generated and published artifacts.

New artifacts live under ``models/<model-id>/<stage>/``. Legacy flat published
artifacts remain readable by model ID during migration.
"""
from __future__ import annotations

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
