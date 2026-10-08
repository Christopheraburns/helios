"""A second domain for the crawler, with nothing in common with retail returns
(CG-9): an outpatient clinic.

Everything here is data: a small warehouse, three kinds of document (an email,
a chat export in a different layout from the practice corpus, a PDF visit
summary), a mapping with an anchor (a visit), crawler settings, and a
hand-built answer key. The crawler's code is not told any of it.

The answer key's offsets are found by searching the text the analyzers produce,
so it checks what is recognised and how it is resolved, not how documents are
split.
"""

from __future__ import annotations

import json
from email.message import EmailMessage
from email.policy import SMTP
from typing import Any

import duckdb
from crawler_samples import pdf_bytes
from helios_core.crawler.settings import CrawlerSettings
from helios_core.ontology.mapping import SourceMapping, resolution_config

DATABASE = "clinic"
CLASSES = ["Patient", "Clinician", "Visit"]

# --- the warehouse --------------------------------------------------------------------

PATIENTS = [
    (10, "MRN-000010", "dana.kim@example.org", "Dana", "Kim"),
    (11, "MRN-000011", "omar.haddad@example.org", "Omar", "Haddad"),
    (12, "MRN-000012", "dana.kim.sr@example.org", "Dana", "Kimura"),
]
CLINICIANS = [(7, "C-07", "Amara Lee"), (8, "C-08", "Ben Ortiz")]
VISITS = [
    (1, "V-2024-0001", 10, 7, "2024-03-01"),
    (2, "V-2024-0002", 11, 8, "2024-03-09"),
    (4, "V-2024-0004", 10, 7, "2024-04-20"),
]


def build_warehouse() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"CREATE SCHEMA {DATABASE}")
    con.execute(
        f"CREATE TABLE {DATABASE}.patients (patient_id BIGINT, mrn VARCHAR, email VARCHAR, "
        "first_name VARCHAR, last_name VARCHAR)"
    )
    con.executemany(f"INSERT INTO {DATABASE}.patients VALUES (?, ?, ?, ?, ?)", PATIENTS)
    con.execute(f"CREATE TABLE {DATABASE}.clinicians (clinician_id BIGINT, staff_code VARCHAR, full_name VARCHAR)")
    con.executemany(f"INSERT INTO {DATABASE}.clinicians VALUES (?, ?, ?)", CLINICIANS)
    con.execute(
        f"CREATE TABLE {DATABASE}.visits (visit_id BIGINT, visit_code VARCHAR, patient_id BIGINT, "
        "clinician_id BIGINT, visit_date DATE)"
    )
    con.executemany(f"INSERT INTO {DATABASE}.visits VALUES (?, ?, ?, ?, ?)", VISITS)
    return con


# --- the mapping ----------------------------------------------------------------------

MAPPING = SourceMapping.model_validate(
    {
        "model": "clinic.ossie.yaml",
        "ontology_version": "clinic@1.0.0",
        "database": DATABASE,
        "entities": [
            {
                "ossie_element": "patients",
                "class": "Patient",
                "identifiers": {
                    "primary": ["patient_id"],
                    "secondary": ["mrn", "email"],
                    "display": ["first_name", "last_name"],
                },
            },
            {
                "ossie_element": "clinicians",
                "class": "Clinician",
                "identifiers": {
                    "primary": ["clinician_id"],
                    "secondary": ["staff_code"],
                    "display": ["full_name"],
                    "alias_templates": ["Dr. {full_name}"],
                },
            },
            {
                "ossie_element": "visits",
                "class": "Visit",
                "identifiers": {"primary": ["visit_id"], "secondary": ["visit_code"]},
            },
        ],
        "anchors": [
            {
                "class": "Visit",
                "lookups": [{"column": "visit_code", "match": "equals", "type": "string", "identifies": "Visit"}],
                "joins": [
                    {"column": "patient_id", "class": "Patient", "edge": "AttendedBy"},
                    {"column": "clinician_id", "class": "Clinician", "edge": "SeenBy"},
                ],
                "date": {"column": "visit_date"},
            }
        ],
    }
)
CONFIG = resolution_config(MAPPING)

# --- the crawler settings ---------------------------------------------------------------

CHAT_LAYOUT = {
    "name": "front desk chat",
    "match": {"export.kind": "front-desk"},
    "messages": "data.events",
    "message_id": "ts",
    "text": "body",
    "sender": "user.id",
    "sender_name": "user.label",
    "timestamp": "at",
    "thread_id": "channel.id",
    "participants": "members",
    "participant_id": "id",
    "participant_role": "kind",
}
VISIT_ROLE = {"class": "Visit", "find": ["case", "in_unit", "single_in_document"]}
PATIENT_ROLE = {
    "class": "Patient",
    "find": [{"case_edge": "AttendedBy"}, "in_unit", "author", "single_in_document", "single_in_case"],
}
SETTINGS = CrawlerSettings.model_validate(
    {
        "analyzers": {"chat": {"layouts": [CHAT_LAYOUT]}},
        "patterns": [
            {"name": "visit_code", "regex": r"\bV-\d{4}-\d{4}\b", "kind": "key", "proposed_class": "Visit", "columns": ["visit_code"]},
            {"name": "record_number", "regex": r"\bMRN-\d{6}\b", "kind": "key", "proposed_class": "Patient", "columns": ["mrn"]},
            {"name": "email_address", "regex": r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "kind": "key", "proposed_class": "Patient", "columns": ["email"]},
            {"name": "visit_date", "regex": r"\b\d{1,2} (?:March|April|May) \d{4}\b", "kind": "value"},
        ],
        "pdf_labels": [
            {"label": "Visit code", "kind": "key", "proposed_class": "Visit", "columns": ["visit_code"]},
            {"label": "Record number", "kind": "key", "proposed_class": "Patient", "columns": ["mrn"]},
        ],
        "contextual": {"Visit": ["the visit", "this appointment"], "Patient": ["the patient"]},
        "header_rules": [{"field": "display_name", "proposed_class": "Patient"}],
        "date_formats": ["%d %B %Y"],
        "about": {"class_priority": ["Visit", "Patient", "Clinician"]},
        "cases": {"identifiers": ["visit_code"]},
        "claims": {
            "predicates": {
                "FOLLOW_UP_REQUESTED": {"subject": PATIENT_ROLE, "object": VISIT_ROLE},
                "PRESCRIPTION_ISSUED": {"subject": VISIT_ROLE, "object": PATIENT_ROLE, "blocked_speakers": ["patient"]},
            },
            "case_classes": ["Visit"],
            "speakers": [
                {"segment": "message", "field": "role", "values": ["patient"], "speaker": "patient"},
                {"segment": "message", "field": "role", "speaker": "clinic"},
                {"segment": "email_body", "author_class": "Patient", "speaker": "patient"},
                {"segment": "page", "speaker": "clinic"},
            ],
            "cues": {
                "FOLLOW_UP_REQUESTED": ["book a follow-up", "follow-up appointment", "see the doctor again"],
                "PRESCRIPTION_ISSUED": ["prescribed", "prescription issued", "prescription:"],
            },
            "negations": ["not", "no", "never"],
            "abbreviations": ["dr"],
        },
        "evaluation": {
            "truth_relationships": {
                "ATTENDED_BY": {"type": "AttendedBy"},
                "SEEN_BY": {"type": "SeenBy"},
            }
        },
    }
)

# --- the documents ----------------------------------------------------------------------


def email(sender: str, subject: str, body: str) -> bytes:
    message = EmailMessage(policy=SMTP)
    message["From"] = sender
    message["To"] = "frontdesk@northside.example"
    message["Subject"] = subject
    message["Date"] = "Mon, 22 Apr 2024 09:00:00 +0000"
    message.set_content(body, charset="utf-8", cte="quoted-printable")
    return message.as_bytes()


def chat(channel: str, *lines: tuple[str, str, str]) -> bytes:
    """Lines are (user id, shown name, text); U-patient is the patient, the rest are staff."""
    return json.dumps(
        {
            "export": {"kind": "front-desk", "version": 2},
            "channel": {"id": channel},
            "members": [{"id": "U-patient", "kind": "patient"}, {"id": "U-desk", "kind": "reception"}],
            "data": {
                "events": [
                    {"ts": f"{channel}.{i}", "user": {"id": user, "label": label}, "body": text, "at": f"2024-04-22T10:0{i}:00Z"}
                    for i, (user, label, text) in enumerate(lines, start=1)
                ]
            },
        }
    ).encode()


DOCUMENTS: dict[str, tuple[str, bytes]] = {
    "dana-email": (
        "message/rfc822",
        email(
            "Dana Kim <dana.kim@example.org>",
            "After my visit V-2024-0004",
            "Hello,\n\nI saw Dr. Amara Lee on 20 April 2024. I would like to book a follow-up "
            "for next month.\n\nThank you,\nDana Kim\n",
        ),
    ),
    "dana-chat": (
        "application/json",
        chat(
            "C-dana",
            ("U-patient", "Dana K.", "Hi, I am asking about visit V-2024-0004."),
            ("U-desk", "Front desk", "Amara Lee prescribed amoxicillin after the visit."),
            ("U-patient", "Dana K.", "Thanks. I was not prescribed anything else, right?"),
        ),
    ),
    "dana-summary": (
        "application/pdf",
        pdf_bytes(
            [
                "Northside Clinic visit summary",
                "Visit code",
                "V-2024-0004",
                "Record number",
                "MRN-000010",
                "Seen by Dr. Amara Lee on 20 April 2024.",
                "Prescription: amoxicillin 500 mg.",
            ]
        ),
    ),
    "omar-email": (
        "message/rfc822",
        email(
            "Omar Haddad <omar.haddad@example.org>",
            "Question about V-2024-0002",
            "Good morning,\n\nAfter the visit with Ben Ortiz I would like to see the doctor again "
            "soon.\n\nRegards,\nOmar Haddad\n",
        ),
    ),
    "omar-summary": (
        "application/pdf",
        pdf_bytes(
            [
                "Northside Clinic visit summary",
                "Visit code",
                "V-2024-0002",
                "Record number",
                "MRN-000011",
                "Seen by Dr. Ben Ortiz on 9 March 2024.",
                "No prescription issued.",
            ]
        ),
    ),
}

# --- the answer key ---------------------------------------------------------------------

ENTITIES = {
    "dana": ("Patient", {"table": "patients", "patient_id": 10}, "Dana Kim"),
    "omar": ("Patient", {"table": "patients", "patient_id": 11}, "Omar Haddad"),
    "lee": ("Clinician", {"table": "clinicians", "clinician_id": 7}, "Amara Lee"),
    "ortiz": ("Clinician", {"table": "clinicians", "clinician_id": 8}, "Ben Ortiz"),
    "visit4": ("Visit", {"table": "visits", "visit_id": 4}, "V-2024-0004"),
    "visit2": ("Visit", {"table": "visits", "visit_id": 2}, "V-2024-0002"),
}
SCENARIO = {"dana-email": "case-dana", "dana-chat": "case-dana", "dana-summary": "case-dana",
            "omar-email": "case-omar", "omar-summary": "case-omar"}
# (document, segment type, text as written, entity, how it is named)
MENTIONS = [
    ("dana-email", "email_header", "Dana Kim", "dana", "direct"),
    ("dana-email", "email_header", "dana.kim@example.org", "dana", "direct"),
    ("dana-email", "email_subject", "V-2024-0004", "visit4", "direct"),
    ("dana-email", "email_body", "Dr. Amara Lee", "lee", "alias"),
    ("dana-email", "email_body", "Dana Kim", "dana", "direct"),
    ("dana-chat", "message", "V-2024-0004", "visit4", "direct"),
    ("dana-chat", "message", "Amara Lee", "lee", "direct"),
    ("dana-chat", "message", "the visit", "visit4", "contextual"),
    ("dana-summary", "page", "V-2024-0004", "visit4", "direct"),
    ("dana-summary", "page", "MRN-000010", "dana", "direct"),
    ("dana-summary", "page", "Dr. Amara Lee", "lee", "alias"),
    ("omar-email", "email_header", "Omar Haddad", "omar", "direct"),
    ("omar-email", "email_header", "omar.haddad@example.org", "omar", "direct"),
    ("omar-email", "email_subject", "V-2024-0002", "visit2", "direct"),
    ("omar-email", "email_body", "the visit", "visit2", "contextual"),
    ("omar-email", "email_body", "Ben Ortiz", "ortiz", "direct"),
    ("omar-email", "email_body", "Omar Haddad", "omar", "direct"),
    ("omar-summary", "page", "V-2024-0002", "visit2", "direct"),
    ("omar-summary", "page", "MRN-000011", "omar", "direct"),
    ("omar-summary", "page", "Dr. Ben Ortiz", "ortiz", "alias"),
]
# (claim, kind, subject, object, [(document, segment type, the sentence that states it)])
CLAIMS = [
    ("claim-followup-dana", "FOLLOW_UP_REQUESTED", "dana", "visit4",
     [("dana-email", "email_body", "I would like to book a follow-up for next month.")]),
    ("claim-rx-dana", "PRESCRIPTION_ISSUED", "visit4", "dana",
     [("dana-chat", "message", "Amara Lee prescribed amoxicillin after the visit."),
      ("dana-summary", "page", "Prescription: amoxicillin 500 mg.")]),
    ("claim-followup-omar", "FOLLOW_UP_REQUESTED", "omar", "visit2",
     [("omar-email", "email_body", "After the visit with Ben Ortiz I would like to see the doctor again soon.")]),
]
RELATIONSHIPS = [
    ("visit4", "ATTENDED_BY", "dana"), ("visit4", "SEEN_BY", "lee"),
    ("visit2", "ATTENDED_BY", "omar"), ("visit2", "SEEN_BY", "ortiz"),
]


def answer_key(segments: list[Any]) -> dict[str, list[dict[str, Any]]]:
    """The ground truth as the scoring harness reads it, with offsets located in
    ``segments`` (the crawl's own, so offsets refer to the text as split)."""
    by_asset: dict[str, list[Any]] = {}
    for segment in sorted(segments, key=lambda s: (s.asset_id, s.ordinal)):
        by_asset.setdefault(segment.asset_id, []).append(segment)

    def locate(document: str, kind: str, text: str, used: set[tuple[str, int]]) -> tuple[Any, int]:
        for segment in by_asset[document]:
            if segment.segment_type != kind:
                continue
            at = segment.text.find(text)
            while at >= 0 and (segment.segment_id, at) in used:
                at = segment.text.find(text, at + 1)
            if at >= 0:
                used.add((segment.segment_id, at))
                return segment, at
        raise AssertionError(f"{text!r} is not in a {kind} segment of {document}")

    def locator(segment: Any) -> dict[str, Any]:
        return {k: v for k, v in segment.locator.items() if k != "line_endings"}

    entities = [
        {"entity_id": name, "entity_type": kind, "source_key": key, "canonical_name": shown}
        for name, (kind, key, shown) in ENTITIES.items()
    ]
    entities += [
        {"entity_id": f"artifact:{d}", "entity_type": "Artifact", "source_key": {"artifact_id": d}, "canonical_name": d}
        for d in DOCUMENTS
    ]
    used: set[tuple[str, int]] = set()
    mentions = []
    for number, (document, kind, text, entity, difficulty) in enumerate(MENTIONS):
        segment, at = locate(document, kind, text, used)
        mentions.append(
            {
                "mention_id": f"tm-{number:02d}",
                "entity_id": entity,
                "artifact_id": document,
                "surface_form": text,
                "start_offset": at,
                "end_offset": at + len(text),
                "scenario_id": SCENARIO[document],
                "entity_type": ENTITIES[entity][0],
                "locator": locator(segment),
                "difficulty": difficulty,
            }
        )
    relationships = [
        {"relationship_id": f"tr-{i}", "source_entity_id": s, "predicate": p, "target_entity_id": t, "artifact_id": None}
        for i, (s, p, t) in enumerate(RELATIONSHIPS)
    ]
    relationships += [
        {
            "relationship_id": f"tr-about-{d}",
            "source_entity_id": f"artifact:{d}",
            "predicate": "DISCUSSES",
            "target_entity_id": "visit4" if SCENARIO[d] == "case-dana" else "visit2",
            "artifact_id": d,
        }
        for d in DOCUMENTS
    ]
    claims, evidence = [], []
    for claim_id, kind, subject, obj, passages in CLAIMS:
        scenario = SCENARIO[passages[0][0]]
        claims.append({"claim_id": claim_id, "scenario_id": scenario, "claim_type": kind, "subject": subject, "obj": obj, "truth_status": "true"})
        for number, (document, segment_kind, text) in enumerate(passages):
            segment, at = locate(document, segment_kind, text, set())
            evidence.append(
                {
                    "evidence_id": f"{claim_id}-e{number}",
                    "claim_id": claim_id,
                    "artifact_id": document,
                    "segment_id": None,
                    "start_offset": at,
                    "end_offset": at + len(text),
                    "locator_type": segment_kind,
                    "locator": locator(segment),
                    "excerpt": text,
                }
            )
    return {
        "entities": entities,
        "entity_mentions": mentions,
        "relationships": relationships,
        "claims": claims,
        "evidence": evidence,
        "expected_queries": [],
    }
