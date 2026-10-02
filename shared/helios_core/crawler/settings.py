"""Crawler settings (CR-0e; docs/crawler-analysis.md section 5).

Operator settings for the deterministic analysis: which analyzers run per asset
type and with which options, identifier patterns, PDF field labels, contextual
phrases, case-linking rules, and claim cue lexicons. Stored as immutable versions
in helios_index.crawler_settings and edited through the API, never in code; every
crawl run records the version it used.

Identity rules (how each class is identified, alias templates, thresholds) are
not here: they belong to the published ontology mapping.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1"


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
        default_factory=list, description="TPC-DS columns to look the value up in"
    )
    ignore_case: bool = False

    @field_validator("regex")
    @classmethod
    def _compiles(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"not a valid regular expression: {exc}") from exc
        return value

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


class CaseLinking(_Model):
    """Which identifiers join documents into one case (step 7)."""

    identifiers: list[str] = Field(description="Pattern names whose values link documents")
    date_window_days: int = Field(30, ge=0, le=3650)


class Claims(_Model):
    """Cue lexicons per claim predicate (step 12)."""

    cues: dict[str, list[str]]
    negations: list[str] = Field(default_factory=list)
    hedges: list[str] = Field(default_factory=list)


# --- the settings document --------------------------------------------------------


class CrawlerSettings(_Model):
    schema_version: Literal["1"] = SCHEMA_VERSION
    analyzers: Analyzers = Field(default_factory=Analyzers)
    patterns: list[PatternRule]
    pdf_labels: list[LabelRule] = Field(default_factory=list)
    contextual: dict[str, list[str]] = Field(
        default_factory=dict, description="Ontology class -> definite phrases referring to it"
    )
    cases: CaseLinking
    claims: Claims

    @model_validator(mode="after")
    def _consistent(self) -> CrawlerSettings:
        names = [p.name for p in self.patterns]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"duplicate pattern names: {duplicates}")
        unknown = sorted(set(self.cases.identifiers) - set(names))
        if unknown:
            raise ValueError(f"cases.identifiers name unknown patterns: {unknown}")
        return self

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


def ontology_problems(
    settings: CrawlerSettings, classes: set[str], predicates: set[str]
) -> list[str]:
    """Settings that name classes or claim predicates the active ontology doesn't have."""
    problems = []
    named = (
        {p.proposed_class for p in settings.patterns}
        | {r.proposed_class for r in settings.pdf_labels}
        | set(settings.contextual)
    )
    for name in sorted(n for n in named if n and n not in classes):
        problems.append(f"unknown ontology class {name!r}")
    for predicate in sorted(set(settings.claims.cues) - predicates):
        problems.append(f"unknown claim predicate {predicate!r}")
    return problems


# --- defaults ----------------------------------------------------------------------
# Written from general retail language. Over-tuning to one source's wording is
# measured on a held-out corpus (Helios-DS C-12).

_MONTH = "(?:January|February|March|April|May|June|July|August|September|October|November|December)"

DEFAULT_SETTINGS = CrawlerSettings(
    patterns=[
        PatternRule(
            name="tpcds_business_id",
            regex=r"\b[A-P]{16}\b",
            kind="key",
            columns=["i_item_id", "c_customer_id", "s_store_id"],
        ),
        PatternRule(
            name="email_address",
            regex=r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
            kind="key",
            proposed_class="Customer",
            columns=["c_email_address"],
        ),
        PatternRule(
            name="ticket_number",
            regex=r"\b(?:receipt\s+)?ticket(?:\s+(?:number|no\.?|#))?\s*:?\s*#?(\d{1,10})\b",
            group=1,
            kind="key",
            proposed_class="Sale",
            columns=["ss_ticket_number", "sr_ticket_number"],
            ignore_case=True,
        ),
        PatternRule(
            name="receipt_tail",
            regex=r"\breceipt\s+ending\s+(?:in\s+)?(\d{3,6})\b",
            group=1,
            kind="partial_key",
            proposed_class="Sale",
            columns=["ss_ticket_number", "sr_ticket_number"],
            ignore_case=True,
        ),
        PatternRule(
            name="return_authorization",
            regex=r"\bRMA-\d{4,10}\b",
            kind="document_id",
            proposed_class="Return",
        ),
        PatternRule(
            name="support_case",
            regex=r"\bCS-\d{4,10}\b",
            kind="document_id",
            proposed_class="Return",
        ),
        PatternRule(name="money", regex=r"\$\d[\d,]*(?:\.\d{2})?", kind="value"),
        PatternRule(name="date", regex=rf"\b{_MONTH}\s+\d{{1,2}},\s+\d{{4}}\b", kind="value"),
    ],
    pdf_labels=[
        LabelRule(label="RMA number", proposed_class="Return", kind="document_id"),
        LabelRule(label="Support case", proposed_class="Return", kind="document_id"),
        LabelRule(label="Store", proposed_class="Store", columns=["s_store_name"], kind="display"),
        LabelRule(
            label="Original receipt ticket",
            proposed_class="Sale",
            columns=["ss_ticket_number", "sr_ticket_number"],
        ),
        LabelRule(label="Customer ID", proposed_class="Customer", columns=["c_customer_id"]),
        LabelRule(label="Name", proposed_class="Customer", kind="display"),
        LabelRule(label="Email", proposed_class="Customer", columns=["c_email_address"]),
        LabelRule(label="Item ID", proposed_class="Item", columns=["i_item_id"]),
        LabelRule(
            label="Description", proposed_class="Item", columns=["i_product_name"], kind="display"
        ),
        LabelRule(label="Brand", proposed_class="Brand", columns=["i_brand"], kind="display"),
        LabelRule(label="Return date", kind="value"),
        LabelRule(label="Sale date", kind="value"),
    ],
    contextual={
        "Item": ["the item", "the product", "the merchandise"],
        "Return": ["this return", "the return"],
        "Customer": ["the customer"],
        "Store": ["that store", "the store"],
    },
    cases=CaseLinking(
        identifiers=["return_authorization", "support_case", "ticket_number", "email_address"],
        date_window_days=30,
    ),
    claims=Claims(
        cues={
            "PACKAGING_DAMAGED": [
                "damaged",
                "damage",
                "crushed",
                "torn",
                "dented",
                "dents",
                "scuffed",
                "scratched",
                "cracked",
                "broken",
                "ripped",
                "impact marks",
            ],
            "RETURN_REASON": ["reason", "reason code", "return slip says", "recorded as"],
            "REFUND_REQUESTED": [
                "refund me",
                "want my",
                "expect a full refund",
                "when will the refund",
                "confirm refund",
                "refund to my card",
                "receive",
                "wants the",
            ],
            "REFUND_APPROVED": [
                "refund approved",
                "approve it",
                "refund is approved",
                "refund of",
                "refund queued",
                "refund issued",
                "processing now",
            ],
        },
        negations=["not", "no", "never", "without", "isn't", "wasn't", "weren't"],
        hedges=["if", "whether", "might", "maybe", "unless"],
    ),
)
