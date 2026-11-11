"""The Ossie document format: the spec version Helios writes, validation against the
vendored schema (ossie-schema.json), and YAML output. Building a document from a
discovery proposal is helios-model's (helios_model.build).
"""
from __future__ import annotations

import json
import os
import re

import jsonschema

SPEC_VERSION = "0.2.0.dev0"
VENDOR = "HELIOS"      # vendor_name of Helios's custom_extensions
DIALECT = "ANSI_SQL"   # the dialect Helios writes expressions in
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "ossie-schema.json")


def validate(doc: dict) -> list[str]:
    """Validate against the vendored Ossie schema plus referential checks the schema cannot express."""
    with open(SCHEMA_PATH) as f:
        schema = json.load(f)
    errors = [f"schema: {e.json_path}: {e.message}" for e in jsonschema.Draft202012Validator(schema).iter_errors(doc)]
    names = {d["name"] for d in doc["datasets"]}
    cols = {d["name"]: {f["name"] for f in d.get("fields", [])} for d in doc["datasets"]}
    for r in doc.get("relationships", []):
        for side in ("from", "to"):
            if r[side] not in names:
                errors.append(f"relationship {r['name']}: unknown dataset {r[side]}")
            else:
                for c in r[f"{side}_columns"]:
                    if c not in cols[r[side]]:
                        errors.append(f"relationship {r['name']}: {r[side]} has no column {c}")
    for m in doc.get("metrics", []):
        for ds, col in re.findall(r"\b([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)\b", m["expression"]["dialects"][0]["expression"]):
            if ds not in cols or col not in cols[ds]:
                errors.append(f"metric {m['name']}: unknown reference {ds}.{col}")
    return errors


def dump_yaml(doc: dict) -> str:
    import yaml
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120)
