"""Crawler settings (CR-0e; docs/crawler-analysis.md section 5).

Operator settings for the deterministic analysis: which analyzers run per asset
type and with which options, identifier patterns, PDF field labels, contextual
phrases, case-linking rules, and claim cue lexicons. Stored as immutable versions
in helios_index.crawler_settings and edited through the API, never in code; every
crawl run records the version it used.

Identity rules (how each class is identified, alias templates, thresholds) are
not here: they belong to the published ontology mapping.

**Schema 2 (CG-1): the engine has no rules of its own.** ``CrawlerSettings()`` is
an empty document: no patterns, labels, phrases, case identifiers or claim cues.
Rules for a kind of data come from a *preset* (``presets/*.yaml``), which is a
complete settings document a user loads, edits and saves as a version. Until
schema 2 one such set was built in; a stored schema 1 document is read as that
set plus whatever the document itself says.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "2"
PRESETS = Path(__file__).parent / "presets"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- analyzers ------------------------------------------------------------------


class PdfAnalyzer(_Model):
    enabled: bool = True
    max_pages: int = Field(50, ge=1, le=10_000, description="Pages beyond this are not read")


class EmailAnalyzer(_Model):
    enabled: bool = True
    identity_headers: list[str] = Field(
        default_factory=lambda: ["From"], description="Headers whose names identify a person"
    )
    html_when_no_plain: bool = Field(
        True, description="Use an HTML body (as text) if no plain part"
    )


class ChatAnalyzer(_Model):
    enabled: bool = True
    schemas: list[str] = Field(
        default_factory=lambda: ["helios-ds/chat-thread/1.0"], description="Accepted chat schemas"
    )


class TextAnalyzer(_Model):
    """Plain text and Markdown files (object stores)."""

    enabled: bool = True
    max_chars: int = Field(2_000_000, ge=1, description="Longer files are cut, and say so")


class RowAnalyzer(_Model):
    """Table rows as documents (the table_rows connector): one segment per text column."""

    enabled: bool = True


class Analyzers(_Model):
    pdf: PdfAnalyzer = Field(default_factory=PdfAnalyzer)
    email: EmailAnalyzer = Field(default_factory=EmailAnalyzer)
    chat: ChatAnalyzer = Field(default_factory=ChatAnalyzer)
    text: TextAnalyzer = Field(default_factory=TextAnalyzer)
    row: RowAnalyzer = Field(default_factory=RowAnalyzer)


# --- mentions -------------------------------------------------------------------

PatternKind = Literal["key", "partial_key", "document_id", "value"]


class PatternRule(_Model):
    """A regular expression that finds a mention.

    - ``key``: the matched text is a value of one of ``columns`` (looked up)
    - ``partial_key``: the matched text is the end of such a value ("receipt ending in 5079")
    - ``document_id``: an identifier that exists only in documents (RMA, case number),
      used to link documents into cases, not looked up in the warehouse
    - ``value``: money, dates: used in joins and claims, not an entity
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    regex: str
    group: int = Field(0, ge=0, description="Capture group holding the value (0 = whole match)")
    kind: PatternKind
    proposed_class: str | None = Field(
        None, description="Ontology class, if known from the pattern"
    )
    columns: list[str] = Field(
        default_factory=list, description="Warehouse columns to look the value up in"
    )
    ignore_case: bool = False
    key_name: str | None = Field(
        None,
        pattern=r"^[a-z][a-z0-9_]*$",
        description=(
            "For a document_id pattern: the name of the key in the entity's document "
            "identifier (documents.<class>:<key_name>=<value>). Default: the pattern's name."
        ),
    )

    @field_validator("regex")
    @classmethod
    def _compiles(cls, value: str) -> str:
        return _valid_regex(value)

    @model_validator(mode="after")
    def _group_exists(self) -> PatternRule:
        groups = re.compile(self.regex).groups
        if self.group > groups:
            raise ValueError(f"group {self.group} but the regex has {groups} group(s)")
        if self.kind in ("key", "partial_key") and not self.columns:
            raise ValueError(f"a {self.kind} pattern needs the columns to look it up in")
        return self


class LabelRule(_Model):
    """A PDF field label and what kind of value follows it."""

    label: str = Field(min_length=1)
    proposed_class: str | None = None
    columns: list[str] = Field(default_factory=list)
    kind: Literal["key", "display", "document_id", "value"] = "key"


def _valid_regex(value: str) -> str:
    try:
        re.compile(value)
    except re.error as exc:
        raise ValueError(f"not a valid regular expression: {exc}") from exc
    return value


class HeaderRule(_Model):
    """A recognised header field whose value names an entity of a class: the
    display name on an email's From line names a person of ``proposed_class``."""

    field: str = Field(min_length=1, description="Field of the header's structure, e.g. display_name")
    proposed_class: str = Field(min_length=1)


class Dictionary(_Model):
    """How the dictionary built from the warehouse judges a name (step 5b). A
    low-specificity name counts only next to a cue for its class, or when the
    document confirms it elsewhere."""

    ordinary_words: list[str] = Field(
        default_factory=list,
        description="Words that are names in the warehouse and also ordinary text",
    )
    short_token: int = Field(5, ge=1, description="A single word shorter than this is not a name alone")
    max_shared_instances: int = Field(
        50, ge=1, description="A name shared by more rows than this is not specific"
    )
    max_form_tokens: int = Field(12, ge=1, description="Longer values are not names")


class About(_Model):
    """What a document is about when no case settles it (step 11)."""

    title_segments: list[str] = Field(
        default_factory=lambda: ["email_subject"],
        description="Segment types that are a document's title; an entity named there wins",
    )
    class_priority: list[str] = Field(
        default_factory=list, description="Classes in order of preference; unlisted come last"
    )


class ResolutionRules(_Model):
    """Tuning of single-mention resolution (step 10). Tier thresholds are in the mapping."""

    max_candidates: int = Field(20, ge=1, description="A name shared by more rows is not resolved")
    close: float = Field(0.05, ge=0, le=1, description="A runner-up this close blocks a definite link")
    low_specificity_factor: float = Field(
        0.5, ge=0, le=1, description="Score factor for an ordinary-word name"
    )
    top_possible: int = Field(3, ge=1, description="How many possible links to keep")


class CaseLinking(_Model):
    """Which identifiers join documents into one case (step 7)."""

    identifiers: list[str] = Field(
        default_factory=list, description="Pattern names whose values link documents"
    )
    date_window_days: int = Field(30, ge=0, le=3650)
    max_hub_documents: int = Field(
        6, ge=1, description="A weak identifier in more documents than this marks no single case"
    )


class CaseEdge(_Model):
    """Find the entity through a relationship of the case's own entity."""

    case_edge: str = Field(min_length=1, description="The relationship type, e.g. PartyTo")


FindStep = Literal["in_unit", "case", "author", "single_in_document", "single_in_case"]


class ClaimRole(_Model):
    """The subject or the object of a claim: its class, and where to look for
    the entity, in order. The first place that gives exactly one entity wins.

    - ``in_unit``: named in the sentence or message that makes the claim
    - ``case``: the entity the case is about (its class must be a case class)
    - ``{case_edge: X}``: related to the case's entity by relationship X
    - ``author``: the document's author, when of this class
    - ``single_in_document`` / ``single_in_case``: the only one of its class there
    """

    class_name: str = Field(alias="class", min_length=1)
    find: list[FindStep | CaseEdge] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ClaimPredicate(_Model):
    """One kind of claim: what it is about, what it points to, and who may make it."""

    subject: ClaimRole
    object: ClaimRole
    blocked_speakers: list[str] = Field(
        default_factory=list, description="Speakers whose words never make this claim"
    )
    unknown_speaker_needs_strong_cue: bool = Field(
        False, description="An unidentified speaker makes it only with a strong cue"
    )


class SpeakerRule(_Model):
    """Who is speaking in a part of a document. The first rule that fits decides;
    with none, the speaker is unknown."""

    segment: str = Field(min_length=1, description="Segment type: message, email_body, page, ...")
    field: str | None = Field(None, description="A field of the segment that must be present")
    values: list[str] = Field(
        default_factory=list, description="... and, if given, have one of these values"
    )
    author_class: str | None = Field(
        None, description="The document's author must be a resolved entity of this class"
    )
    speaker: str = Field(min_length=1)


class Confidence(_Model):
    strong: float = Field(0.95, ge=0, le=1)
    medium: float = Field(0.85, ge=0, le=1)
    weak: float = Field(0.7, ge=0, le=1)


class Claims(_Model):
    """Claims (step 12): the kinds there are, the cue phrases that state each,
    who is speaking, and what cancels a cue."""

    predicates: dict[str, ClaimPredicate] = Field(
        default_factory=dict, description="Claim type -> its subject, object and speakers"
    )
    case_classes: list[str] = Field(
        default_factory=list, description="Classes of entity a case can be about"
    )
    speakers: list[SpeakerRule] = Field(default_factory=list)
    cues: dict[str, list[str]] = Field(default_factory=dict)
    weak_words: list[str] = Field(
        default_factory=list,
        description="One-word cues too general to stand alone: they count only when the unit names the claim's subject",
    )
    negations: list[str] = Field(default_factory=list)
    hedges: list[str] = Field(default_factory=list)
    negation_window: int = Field(3, ge=0, le=20, description="Words before a cue a negation reaches over")
    gap_words: int = Field(4, ge=0, le=20, description="Words a * in a cue phrase may stand for")
    abbreviations: list[str] = Field(
        default_factory=list, description="Words whose trailing period does not end a sentence"
    )
    confidence: Confidence = Field(default_factory=Confidence)


class LlmExtraction(_Model):
    """The LLM crawler (strategy ``llm``, CR-L1). The instructions are the
    versioned part of its prompt; the ontology classes, claim vocabulary and
    reply format are added from the ontology at run time. The model itself is
    the project's default provider, not a setting."""

    prompt_version: str = Field("llm-1", pattern=r"^[A-Za-z0-9._-]{1,40}$")
    instructions: str = Field(
        "You read documents and list what they mention.\n"
        "- List every reference to an entity of the classes below: names, identifiers, "
        "and phrases that refer to one.\n"
        "- Quote each reference exactly as it appears, character for character, and "
        "keep the quote as short as the reference itself.\n"
        "- List a claim only when the text states it. Its quote is the sentence or "
        "message that states it; its subject and object are entities you listed.\n"
        "- Never include anything that is not written in the document.",
        min_length=1,
        max_length=8000,
    )
    max_asset_chars: int = Field(
        24_000, ge=500, le=400_000, description="Longer documents are cut, and counted"
    )
    concurrency: int = Field(2, ge=1, le=16, description="Documents sent to the model at once")
    input_price_per_million: float | None = Field(
        None, ge=0, description="USD per million input tokens, to report cost; unset = no cost"
    )
    output_price_per_million: float | None = Field(None, ge=0)


# --- the settings document --------------------------------------------------------


class CrawlerSettings(_Model):
    schema_version: Literal["2"] = SCHEMA_VERSION
    analyzers: Analyzers = Field(default_factory=Analyzers)
    patterns: list[PatternRule] = Field(default_factory=list)
    pdf_labels: list[LabelRule] = Field(default_factory=list)
    contextual: dict[str, list[str]] = Field(
        default_factory=dict, description="Ontology class -> definite phrases referring to it"
    )
    class_cues: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Ontology class -> regular expressions (case-insensitive) that, near a "
            "low-specificity name, confirm it names that class. A class with none always counts."
        ),
    )
    cue_window: int = Field(40, ge=1, le=1000, description="Characters either side of a name")
    header_rules: list[HeaderRule] = Field(default_factory=list)
    dictionary: Dictionary = Field(default_factory=Dictionary)
    date_formats: list[str] = Field(
        default_factory=lambda: ["%Y-%m-%d"], description="strptime formats for dates in text"
    )
    about: About = Field(default_factory=About)
    resolution: ResolutionRules = Field(default_factory=ResolutionRules)
    cases: CaseLinking = Field(default_factory=CaseLinking)
    claims: Claims = Field(default_factory=Claims)
    llm: LlmExtraction = Field(default_factory=LlmExtraction)

    @model_validator(mode="before")
    @classmethod
    def _from_schema_1(cls, data: Any) -> Any:
        """A schema 1 document relied on rules that were built in. Read it as
        those rules with the document's own sections on top."""
        if isinstance(data, dict) and data.get("schema_version") == "1":
            legacy = schema_1_defaults()
            merged = {**legacy, **data, "schema_version": SCHEMA_VERSION}
            # Per-pattern values that the code used to decide from the pattern's name.
            by_name = {p["name"]: p for p in legacy.get("patterns", [])}
            merged["patterns"] = [
                {**{k: v for k, v in by_name.get(p.get("name"), {}).items() if k == "key_name"}, **p}
                for p in merged.get("patterns", [])
            ]
            # A schema 1 claims section held only cue words; the kinds of claim were in code.
            merged["claims"] = {**legacy.get("claims", {}), **(data.get("claims") or {})}
            return merged
        return data

    def is_empty(self) -> bool:
        """No rules at all: a crawl with these settings finds only what the
        warehouse dictionary names."""
        return not (
            self.patterns or self.pdf_labels or self.contextual or self.cases.identifiers
            or self.claims.cues or self.class_cues or self.header_rules
        )

    @model_validator(mode="after")
    def _consistent(self) -> CrawlerSettings:
        names = [p.name for p in self.patterns]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"duplicate pattern names: {duplicates}")
        unknown = sorted(set(self.cases.identifiers) - set(names))
        if unknown:
            raise ValueError(f"cases.identifiers name unknown patterns: {unknown}")
        for class_name, cues in self.class_cues.items():
            for cue in cues:
                try:
                    _valid_regex(cue)
                except ValueError as exc:
                    raise ValueError(f"class_cues.{class_name}: {cue!r}: {exc}") from exc
        return self

    def cue_pattern(self, class_name: str) -> re.Pattern[str] | None:
        """One pattern matching any of the class's cues, or None if it has none."""
        cues = self.class_cues.get(class_name)
        return _cue_pattern(tuple(cues)) if cues else None

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


@lru_cache(maxsize=256)
def _cue_pattern(cues: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile("|".join(cues), re.IGNORECASE)


def ontology_problems(
    settings: CrawlerSettings, classes: set[str], predicates: set[str]
) -> list[str]:
    """Settings that name classes or claim predicates the active ontology doesn't have."""
    problems = []
    named = (
        {p.proposed_class for p in settings.patterns}
        | {r.proposed_class for r in settings.pdf_labels}
        | set(settings.contextual)
        | set(settings.class_cues)
        | {r.proposed_class for r in settings.header_rules}
        | set(settings.about.class_priority)
        | set(settings.claims.case_classes)
        | {r.author_class for r in settings.claims.speakers}
    )
    for predicate in settings.claims.predicates.values():
        for role in (predicate.subject, predicate.object):
            named.add(role.class_name)
            named.update(step.case_edge for step in role.find if isinstance(step, CaseEdge))
    for name in sorted(n for n in named if n and n not in classes):
        problems.append(f"unknown ontology class {name!r}")
    for predicate in sorted((set(settings.claims.cues) | set(settings.claims.predicates)) - predicates):
        problems.append(f"unknown claim predicate {predicate!r}")
    return problems


# --- presets ------------------------------------------------------------------------
# Rules for a kind of data, shipped as data. The engine names none of them.


@lru_cache(maxsize=1)
def _preset_index() -> dict[str, Any]:
    return yaml.safe_load((PRESETS / "index.yaml").read_text()) or {}


def presets() -> dict[str, dict[str, str]]:
    """name -> {title, description} of each shipped preset."""
    return {
        name: {"title": str(info.get("title", name)), "description": str(info.get("description", ""))}
        for name, info in sorted((_preset_index().get("presets") or {}).items())
        if (PRESETS / f"{name}.yaml").is_file()
    }


def preset_document(name: str) -> dict[str, Any]:
    if name not in presets():
        raise KeyError(name)
    return yaml.safe_load((PRESETS / f"{name}.yaml").read_text())


def load_preset(name: str) -> CrawlerSettings:
    return CrawlerSettings.model_validate(preset_document(name))


def schema_1_defaults() -> dict[str, Any]:
    """The rules that were built in before schema 2, as a document."""
    return dict(preset_document(str(_preset_index()["schema_1_defaults"])))


EMPTY_SETTINGS = CrawlerSettings()
