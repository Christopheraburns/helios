"""Step 5b of the analysis (docs/crawler-analysis.md): a dictionary of the
surface forms that name instances of each ontology class, built from the
warehouse through the ontology mapping and nothing else.

The mapping says, per class, which table holds its instances and which columns
identify them: exact keys (``secondary``), the display name (``display``),
alias columns and composite ``alias_templates`` ("{c_salutation} {c_last_name}").
The gazetteer reads those columns, normalises every value into a form, and
remembers which instances share it. The crawler has no table knowledge of its
own: a different mapping gives a different gazetteer.

Every form carries a specificity: how many instances share it and whether it
looks like an ordinary word. TPC-DS names stores ``ought``, ``able`` and ``bar``;
such forms only count as mentions next to a context cue (``mentions.py``).

A class with two or more display columns (first name, surname) also gets each
column alone as a *partial* form ("Tonya", "Raymond"): the way an email is signed
or a chat refers to someone already introduced. A partial form is inherently
low-specificity and counts only when the same asset anchors one of its instances
by a key or a full name (``mentions.py``).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from functools import lru_cache

from helios_core.crawler.settings import Dictionary
from helios_core.index import ids
from helios_core.ontology.mapping import ClassIdentifiers, ResolutionConfig, template_columns

KINDS = ("key", "display", "alias", "template")
KIND_RANK = {kind: rank for rank, kind in enumerate(KINDS)}
# How a name is judged (ordinary words, how short or how widely shared is too
# much) comes from the settings' ``dictionary`` section.

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
# A token is a run of word characters that may carry '.', '@', '+' or '-' inside
# (email addresses, RMA numbers), or one punctuation character.
_TOKEN = re.compile(r"[^\W_][\w.@+-]*[^\W_]|[^\W_]|_|[^\s\w]")
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Form:
    """One surface form of one class, as the warehouse spells it."""

    surface: str  # normalised: lower case, single spaces, no trailing punctuation
    class_name: str
    kind: str  # key, display, alias or template
    columns: str  # source columns, comma-separated
    instances: tuple[str, ...]  # external IDs of the instances sharing the form
    low_specificity: bool
    partial: bool = False  # some name components missing (a bare first name or surname)

    @property
    def specificity(self) -> float:
        return 1.0 / len(self.instances) if self.instances else 0.0


@dataclass(frozen=True)
class GazetteerHit:
    surface: str  # as written in the text
    start: int
    end: int
    class_name: str
    kind: str
    columns: str
    instances: tuple[str, ...]
    low_specificity: bool
    partial: bool = False


def normalise(value: str) -> str:
    return _SPACE.sub(" ", value.strip().lower()).rstrip(".,;:!?")


def tokens(text: str) -> list[tuple[str, int, int]]:
    """(token, start, end) for every token in ``text``, lower-cased."""
    return [(m.group().lower(), m.start(), m.end()) for m in _TOKEN.finditer(text)]


def _token_strings(text: str) -> tuple[str, ...]:
    return tuple(t for t, _, _ in tokens(text))


def _columns_of(identifiers: ClassIdentifiers) -> list[str]:
    """Every column the class's forms are built from, in a stable order."""
    seen: dict[str, None] = {}
    for column in (
        *identifiers.primary,
        *identifiers.secondary,
        *identifiers.display,
        *identifiers.aliases,
        *(c for t in identifiers.alias_templates for c in template_columns(t)),
    ):
        seen.setdefault(column, None)
    return list(seen)


def has_names(identifiers: ClassIdentifiers) -> bool:
    """Whether the class has any identifier column beyond its primary key. Sale
    and Return are known only by ticket numbers, which patterns find; their
    tables are far too large to list and would add no names."""
    primary = set(identifiers.primary)
    named = (
        set(identifiers.secondary)
        | set(identifiers.display)
        | set(identifiers.aliases)
        | {c for t in identifiers.alias_templates for c in template_columns(t)}
    )
    return bool(named - primary)


class _Builder:
    """Collects forms per (class, surface) while rows stream in."""

    def __init__(self, rules: Dictionary | None = None) -> None:
        self.rules = rules or Dictionary()
        self.kind: dict[tuple[str, str], str] = {}
        self.columns: dict[tuple[str, str], str] = {}
        self.instances: dict[tuple[str, str], dict[str, None]] = defaultdict(dict)
        self.partial: dict[tuple[str, str], bool] = {}

    def add(
        self, class_name: str, value: str, kind: str, columns: str, instance: str, partial: bool
    ) -> None:
        surface = normalise(value)
        if not surface or len(_token_strings(surface)) > self.rules.max_form_tokens:
            return
        key = (class_name, surface)
        # One entry per form: the most specific kind wins, instances accumulate.
        if key not in self.kind or KIND_RANK[kind] < KIND_RANK[self.kind[key]]:
            self.kind[key], self.columns[key] = kind, columns
            self.partial[key] = partial
        elif KIND_RANK[kind] == KIND_RANK[self.kind[key]]:
            self.partial[key] = self.partial[key] and partial
        self.instances[key][instance] = None

    def forms(self) -> list[Form]:
        result = []
        for key in sorted(self.kind):
            class_name, surface = key
            instances = tuple(self.instances[key])
            result.append(
                Form(
                    surface=surface,
                    class_name=class_name,
                    kind=self.kind[key],
                    columns=self.columns[key],
                    instances=instances,
                    low_specificity=is_low_specificity(
                        surface, len(instances), self.partial[key], self.rules
                    ),
                    partial=self.partial[key],
                )
            )
        return result


def is_low_specificity(
    surface: str, instance_count: int, partial: bool = False, rules: Dictionary | None = None
) -> bool:
    """A form that would match ordinary text or many instances: a short single
    token, an ordinary word, a partial name (some components missing), or one
    shared by many instances."""
    rules = rules or Dictionary()
    ordinary = _ordinary(tuple(rules.ordinary_words))
    parts = _token_strings(surface)
    if len(parts) == 1 and len(parts[0]) < rules.short_token:
        return True
    if surface in ordinary or all(p in ordinary for p in parts):
        return True
    return partial or instance_count > rules.max_shared_instances


@lru_cache(maxsize=8)
def _ordinary(words: tuple[str, ...]) -> frozenset[str]:
    return frozenset(words)


def _render(template: str, row: dict[str, Any]) -> tuple[str, bool]:
    """A template with its columns filled in; empty when every component is
    missing, and flagged partial when some are."""
    columns = template_columns(template)
    values = {c: str(row.get(c) or "").strip() for c in columns}
    present = [c for c in columns if values[c]]
    if not present:
        return "", True
    rendered = template
    for column in columns:
        rendered = rendered.replace("{" + column + "}", values[column])
    return rendered, len(present) < len(columns)


def _forms_of_row(
    builder: _Builder, identifiers: ClassIdentifiers, row: dict[str, Any], instance: str
) -> None:
    class_name = identifiers.class_name
    for column in identifiers.secondary:
        if column not in identifiers.primary and row.get(column) not in (None, ""):
            builder.add(class_name, str(row[column]), "key", column, instance, False)
    if identifiers.display:
        display, partial = _render(" ".join(f"{{{c}}}" for c in identifiers.display), row)
        if display:
            builder.add(
                class_name, display, "display", ",".join(identifiers.display), instance, partial
            )
    if len(identifiers.display) >= 2:  # each component alone: a partial name
        for column in identifiers.display:
            if row.get(column) not in (None, ""):
                builder.add(class_name, str(row[column]), "display", column, instance, True)
    for column in identifiers.aliases:
        if row.get(column) not in (None, ""):
            builder.add(class_name, str(row[column]), "alias", column, instance, False)
    for template in identifiers.alias_templates:
        rendered, partial = _render(template, row)
        if rendered:
            builder.add(
                class_name,
                rendered,
                "template",
                ",".join(template_columns(template)),
                instance,
                partial,
            )


class Gazetteer:
    def __init__(self, forms: Iterable[Form], classes: Iterable[str], config: ResolutionConfig):
        self.classes = sorted(set(classes))
        self.config = config
        self.forms: dict[tuple[str, str], Form] = {}
        self._by_first_token: dict[str, list[tuple[tuple[str, ...], Form]]] = defaultdict(list)
        self._keys: dict[str, dict[str, Form]] = defaultdict(dict)
        self.column_classes = {
            column: name
            for name, identifiers in config.classes.items()
            for column in _columns_of(identifiers)
        }
        for form in forms:
            self.forms[(form.class_name, form.surface)] = form
            parts = _token_strings(form.surface)
            if parts:
                self._by_first_token[parts[0]].append((parts, form))
            if form.kind == "key":
                self._keys[form.columns][form.surface] = form
        for entries in self._by_first_token.values():
            entries.sort(key=lambda e: (-len(e[0]), KIND_RANK[e[1].kind], e[1].class_name))

    # --- building ---------------------------------------------------------------------

    @classmethod
    def from_rows(
        cls,
        rows_by_class: dict[str, list[dict[str, Any]]],
        config: ResolutionConfig,
        dictionary: Dictionary | None = None,
    ) -> Gazetteer:
        """Build from row dicts (column -> value) per class: what ``build`` fetches,
        and what tests supply directly. ``dictionary`` is the settings' section
        of that name; without it no word is ordinary."""
        builder = _Builder(dictionary)
        for class_name, rows in rows_by_class.items():
            identifiers = config.classes[class_name]
            columns = _columns_of(identifiers)
            seen: set[tuple[Any, ...]] = set()
            for row in rows:
                primary = tuple(row.get(c) for c in identifiers.primary)
                values = tuple(row.get(c) for c in columns)
                if values in seen or any(v in (None, "") for v in primary):
                    continue
                seen.add(values)  # a repeated primary key with new values adds forms
                instance = ids.external_id(
                    config.database,
                    identifiers.ossie_element,
                    dict(zip(identifiers.primary, primary, strict=True)),
                )
                _forms_of_row(builder, identifiers, row, instance)
        return cls(builder.forms(), rows_by_class, config)

    @classmethod
    def build(
        cls,
        cursor_factory: Callable[[], Any],
        config: ResolutionConfig,
        classes: list[str],
        dictionary: Dictionary | None = None,
    ) -> Gazetteer:
        """Read each class's identifier columns from its table through ``cursor_factory``,
        in the mapping's database."""
        rows_by_class: dict[str, list[dict[str, Any]]] = {}
        for class_name in classes:
            identifiers = config.classes.get(class_name)
            if identifiers is None or not has_names(identifiers):
                continue
            rows_by_class[class_name] = fetch_rows(cursor_factory, identifiers, config.database)
        return cls.from_rows(rows_by_class, config, dictionary)

    def to_dict(self) -> dict[str, Any]:
        return {
            "classes": self.classes,
            "forms": [
                {
                    "surface": f.surface,
                    "class": f.class_name,
                    "kind": f.kind,
                    "columns": f.columns,
                    "instances": list(f.instances),
                    "low_specificity": f.low_specificity,
                    "partial": f.partial,
                }
                for f in self.forms.values()
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], config: ResolutionConfig) -> Gazetteer:
        forms = [
            Form(
                surface=f["surface"],
                class_name=f["class"],
                kind=f["kind"],
                columns=f["columns"],
                instances=tuple(f["instances"]),
                low_specificity=bool(f["low_specificity"]),
                partial=bool(f.get("partial", False)),
            )
            for f in data["forms"]
        ]
        return cls(forms, data["classes"], config)

    # --- querying ----------------------------------------------------------------------

    @property
    def key_forms_by_column(self) -> dict[str, dict[str, Form]]:
        """column -> normalised key value -> form, for classifying pattern-found keys."""
        return self._keys

    def lookup(self, key_column: str, value: str) -> Form | None:
        return self._keys.get(key_column, {}).get(normalise(value))

    def class_of_key(self, columns: Iterable[str], value: str) -> str | None:
        """The class whose ``columns`` hold ``value`` as a key, if any."""
        for column in columns:
            form = self.lookup(column, value)
            if form is not None:
                return form.class_name
        return None

    def scan(self, text: str) -> list[GazetteerHit]:
        """Every form in ``text``, longest match first on token boundaries. Overlaps
        keep the longest hit, then the most specific kind; equal spans of
        different classes are all kept."""
        toks = tokens(text)
        strings = [t for t, _, _ in toks]
        hits: list[GazetteerHit] = []
        for i, first in enumerate(strings):
            for parts, form in self._by_first_token.get(first, ()):
                n = len(parts)
                if i + n <= len(strings) and tuple(strings[i : i + n]) == parts:
                    start, end = toks[i][1], toks[i + n - 1][2]
                    hits.append(
                        GazetteerHit(
                            text[start:end],
                            start,
                            end,
                            form.class_name,
                            form.kind,
                            form.columns,
                            form.instances,
                            form.low_specificity,
                            form.partial,
                        )
                    )
        return _resolve_overlaps(hits)


def _resolve_overlaps(hits: list[GazetteerHit]) -> list[GazetteerHit]:
    """Longest hit first; a shorter hit inside or across a kept one is dropped,
    except an equal span naming a different class."""
    ordered = sorted(
        hits, key=lambda h: (h.start - h.end, KIND_RANK[h.kind], h.start, h.class_name)
    )
    kept: list[GazetteerHit] = []
    for hit in ordered:
        clashing = [k for k in kept if k.start < hit.end and hit.start < k.end]
        if any(
            (k.start, k.end) != (hit.start, hit.end) or k.class_name == hit.class_name
            for k in clashing
        ):
            continue
        kept.append(hit)
    return sorted(kept, key=lambda h: (h.start, h.end, h.class_name))


def fetch_rows(
    cursor_factory: Callable[[], Any], identifiers: ClassIdentifiers, source_schema: str
) -> list[dict[str, Any]]:
    """``SELECT DISTINCT <identifier columns> FROM <schema>.<table>``. Names are
    validated as plain identifiers; nothing from a document is interpolated."""
    columns = _columns_of(identifiers)
    for name in (source_schema, identifiers.ossie_element, *columns):
        if not _IDENTIFIER.match(name):
            raise ValueError(f"not a plain SQL identifier: {name!r}")
    cursor = cursor_factory()
    cursor.execute(
        f"SELECT DISTINCT {', '.join(columns)} FROM {source_schema}.{identifiers.ossie_element}"
    )
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


__all__ = [
    "Form",
    "Gazetteer",
    "GazetteerHit",
    "fetch_rows",
    "has_names",
    "is_low_specificity",
    "normalise",
    "tokens",
]
