"""Finds retail-specific "shapes" in crawler source code (CG-0).

A shape is knowledge of one customer's domain written into code: a class or
relationship name, a claim type, a table or column, or a domain word. The
terms are not typed here by hand: they are read from the shipped retail
ontology, the shipped warehouse mapping and the default crawler settings, so
the list is exactly what a different customer would configure differently.

Only code counts: string constants and the names of variables, functions,
attributes and arguments. Comments and docstrings may explain the retail
example freely.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

import yaml
from helios_core.ontology.mapping import load_mapping, template_columns
from crawler_samples import RETAIL_SETTINGS

REPO_ROOT = Path(__file__).resolve().parents[3]
# The crawler's code: the package itself and the shared settings model.
SCANNED = ("apps/helios/crawler", "shared/helios_core/crawler")

# Ordinary words that are also retail class names or vocabulary. They are a
# shape only where they are unmistakably the retail thing: a string constant
# that is exactly the class name ("Store"), never a lower-case word or a
# variable name (``store`` is also the index store, ``item`` a loop variable,
# ``reason`` any reason).
AMBIGUOUS = frozenset(
    {"item", "store", "reason", "contains", "warehouse", "promotion", "name", "date", "email",
     "description", "money", "case", "number", "address", "business", "support", "original",
     "tail", "authorization", "id", "the", "this", "that"}
)
# Words of the retail example that no class, label or pattern name spells out.
EXTRA_WORDS = frozenset(
    {"retail", "tpcds", "rma", "shopper", "buyer", "staff", "salutation", "refund", "damage",
     "damaged", "packaging"}
)
# TPC-DS's date dimension, which the mapping has no class for.
EXTRA_IDENTIFIERS = frozenset(
    {"tpcds", "date_dim", "d_date_sk", "d_date", "sr_returned_date_sk", "ss_sold_date_sk"}
)

_WORD = re.compile(r"[A-Za-z][a-z]+|[A-Z]+(?![a-z])")
_SQL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def terms() -> dict[str, frozenset[str]]:
    """The retail example's vocabulary, by kind."""
    mapping = load_mapping(REPO_ROOT / "ontology/mappings/ossie/tpcds.yaml")
    pack = yaml.safe_load((REPO_ROOT / "ontology/packs/retail/retail.yaml").read_text())
    extension = yaml.safe_load(
        (REPO_ROOT / "ontology/customers/example-tenant/extension.yaml").read_text()
    )
    classes = (
        set(pack.get("classes") or {})
        | set(extension.get("classes") or {})
        | {e.class_name for e in mapping.entities}
        | {r.edge for r in mapping.relationships}
    )
    predicates = {
        value
        for enum in (pack.get("enums") or {}).values()
        for value in (enum.get("permissible_values") or {})
    }
    identifiers = set(EXTRA_IDENTIFIERS) | {e.ossie_element for e in mapping.entities}
    for entity in mapping.entities:
        ids = entity.identifiers
        identifiers |= {*ids.primary, *ids.secondary, *ids.display, *ids.aliases}
        identifiers |= {c for t in ids.alias_templates for c in template_columns(t)}
    for relationship in mapping.relationships:
        identifiers |= set(relationship.ossie_relationship.split("__")[:2])
    settings = RETAIL_SETTINGS
    # Values of the default (retail) rules: a string constant equal to one is that rule in code.
    preset = {p.name for p in settings.patterns} | {p.regex for p in settings.patterns}
    preset |= {label.label for label in settings.pdf_labels}
    preset |= {phrase for found in settings.contextual.values() for phrase in found}
    preset |= {cue for cues in settings.claims.cues.values() for cue in cues}
    preset |= set(settings.claims.negations) | set(settings.claims.hedges)
    # Distinctive nouns: from class names, pattern names, labels and contextual phrases.
    named = [p.name.replace("_", " ") for p in settings.patterns]
    named += [label.label for label in settings.pdf_labels]
    named += [phrase for found in settings.contextual.values() for phrase in found]
    words = set(EXTRA_WORDS) | {c.lower() for c in classes}
    for phrase in named:
        words |= {w.lower() for w in re.findall(r"[A-Za-z]{3,}", phrase)}
    return {
        "class": frozenset(classes),
        "claim": frozenset(predicates),
        "identifier": frozenset(i for i in identifiers if "_" in i or i in EXTRA_IDENTIFIERS),
        # A one-word rule ("approved", "date") is also an ordinary string, so only
        # rules of several words, or with an underscore, are matched as whole values.
        "preset": frozenset(
            v for v in preset - classes - predicates if re.search(r"[\s_]", v.strip()) and len(v) > 3
        ),
        "word": frozenset(words - AMBIGUOUS),
    }


def _docstrings(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    found.add(id(body[0].value))
    return found


def shapes_in(source: str, vocabulary: dict[str, frozenset[str]] | None = None) -> Counter[str]:
    """term -> how many times the code uses it, as ``kind:term``
    (``class:Return``, ``identifier:store_returns``, ``word:refund``,
    ``preset:the item``)."""
    vocabulary = vocabulary or terms()
    tree = ast.parse(source)
    skip = _docstrings(tree)
    found: Counter[str] = Counter()
    lower_classes = {c.lower(): c for c in vocabulary["class"]}

    def word(raw: str) -> str | None:
        """The vocabulary word ``raw`` is, allowing a plural."""
        lower = raw.lower()
        for candidate in (lower, lower[:-1] if lower.endswith("s") else lower):
            if candidate in vocabulary["word"]:
                return candidate
        return None

    def words(name: str) -> None:
        for raw in _WORD.findall(name):
            base = word(raw)
            if base is not None:
                # A class used in a name (RETURN_CLASS, sale_key) reads as that class.
                found[f"class:{lower_classes[base]}" if base in lower_classes else f"word:{base}"] += 1

    def text(value: str) -> None:
        if value in vocabulary["class"]:
            found[f"class:{value}"] += 1
            return
        if value in vocabulary["preset"]:
            found[f"preset:{value}"] += 1
            return
        for name in _SQL_NAME.findall(value):
            if name in vocabulary["claim"]:
                found[f"claim:{name}"] += 1
            elif name.lower() in vocabulary["identifier"]:
                found[f"identifier:{name.lower()}"] += 1
            else:
                words(name)

    def identifier(name: str) -> None:
        if name in vocabulary["claim"]:
            found[f"claim:{name}"] += 1
        elif name.lower() in vocabulary["identifier"]:
            found[f"identifier:{name.lower()}"] += 1
        else:
            words(name)

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            text(node.value)
        elif isinstance(node, ast.Name):
            identifier(node.id)
        elif isinstance(node, ast.Attribute):
            identifier(node.attr)
        elif isinstance(node, ast.arg):
            identifier(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            identifier(node.arg)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            identifier(node.name)
    return found


def scan() -> dict[str, dict[str, int]]:
    """file -> {term: count}, for every scanned file that has any."""
    vocabulary = terms()
    result: dict[str, dict[str, int]] = {}
    for directory in SCANNED:
        for path in sorted((REPO_ROOT / directory).rglob("*.py")):
            found = shapes_in(path.read_text(), vocabulary)
            if found:
                result[str(path.relative_to(REPO_ROOT))] = dict(sorted(found.items()))
    return result
