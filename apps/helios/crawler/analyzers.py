"""Steps 2 and 3 of the analysis (docs/crawler-analysis.md): detect each asset's
type and split it into segments, the smallest parts evidence can point to.

Locators follow the Helios-DS ground-truth conventions, so crawler output and the
answer key compare directly:

- email: ``{"header": "From"}``, ``{"part": "subject"}``, ``{"part": "body"}``. Body
  text uses "\\n" line endings, as ground-truth body offsets do.
- chat: ``{"message_id": ...}``, one segment per message.
- PDF: ``{"page": n}`` (1-based), one segment per page, text as extracted.
- plain text: ``{"part": "text"}``, the whole file.
- table row: ``{"column": name}``, one segment per text column.

PDF pages also get ``fields``: label/value pairs (a known field label on one line,
its value on the next) and table rows (a run of column headers followed by one
value per header). Labels come from the crawler settings (``pdf_labels``), so
the same rules apply to any report layout that uses them.
"""

from __future__ import annotations

import email
import email.policy
import html
import io
import json
import re
from dataclasses import dataclass, field
from typing import Any

from helios_core.crawler.settings import CrawlerSettings

# Declared MIME type -> analyzer.
ANALYZER_FOR_MIME = {
    "application/pdf": "pdf",
    "message/rfc822": "email",
    "application/json": "chat",
    "text/plain": "text",
    "text/markdown": "text",
    "application/x-helios-row+json": "row",
}


@dataclass
class Segment:
    segment_type: str
    locator: dict[str, Any]
    text: str
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class Analysis:
    """``status``: analyzed, unsupported, type_mismatch, no_text or invalid."""

    status: str
    detail: str = ""
    segments: list[Segment] = field(default_factory=list)


def detect(data: bytes) -> str | None:
    """The asset type the bytes actually are: pdf, chat, email, or None."""
    head = data[:1024].lstrip()
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"{"):
        try:
            document = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            return None
        if isinstance(document, dict) and "messages" in document:
            return "chat"
        if isinstance(document, dict) and "row_key" in document and "text" in document:
            return "row"
        return None
    try:
        message = email.message_from_bytes(data, policy=email.policy.default)
        if message["From"] or message["Subject"]:
            return "email"
    except Exception:  # noqa: BLE001, S110 - anything unparseable is simply not an email
        pass
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return "text" if "\x00" not in text else None


def analyze_asset(mime_type: str, data: bytes, settings: CrawlerSettings) -> Analysis:
    declared = ANALYZER_FOR_MIME.get(mime_type)
    if declared is None:
        return Analysis("unsupported", f"no analyzer for {mime_type}")
    actual = detect(data)
    if actual != declared:
        return Analysis(
            "type_mismatch", f"declared {mime_type}, content looks like {actual or 'unknown'}"
        )
    analyzer = getattr(settings.analyzers, declared)
    if not analyzer.enabled:
        return Analysis("unsupported", f"the {declared} analyzer is disabled in the settings")
    try:
        if declared == "email":
            return _email(data, settings)
        if declared == "chat":
            return _chat(data, settings)
        if declared == "text":
            return _text(data, settings)
        if declared == "row":
            return _row(data)
        return _pdf(data, settings)
    except Exception as exc:  # noqa: BLE001 - a malformed asset is recorded, not fatal
        return Analysis("invalid", f"{type(exc).__name__}: {exc}"[:500])


# --- email -----------------------------------------------------------------------------


def _email(data: bytes, settings: CrawlerSettings) -> Analysis:
    message = email.message_from_bytes(data, policy=email.policy.default)
    segments: list[Segment] = []
    for header in settings.analyzers.email.identity_headers:
        value = message[header]
        if value is None:
            continue
        addresses = getattr(value, "addresses", ())
        for address in addresses or ():
            shown = (
                f"{address.display_name} <{address.addr_spec}>"
                if address.display_name
                else address.addr_spec
            )
            segments.append(
                Segment(
                    "email_header",
                    {"header": header},
                    shown,
                    {"display_name": address.display_name, "address": address.addr_spec},
                )
            )
    subject = str(message["Subject"] or "")
    segments.append(
        Segment("email_subject", {"part": "subject"}, subject, {"date": str(message["Date"] or "")})
    )
    part = message.get_body(preferencelist=("plain",))
    body, source = "", "plain"
    if part is not None:
        body = part.get_content()
    elif settings.analyzers.email.html_when_no_plain:
        html_part = message.get_body(preferencelist=("html",))
        if html_part is not None:
            body, source = _html_to_text(html_part.get_content()), "html"
    body = body.replace("\r\n", "\n")
    if body.strip():
        segments.append(
            Segment("email_body", {"part": "body", "line_endings": "LF"}, body, {"source": source})
        )
    if not any(s.text.strip() for s in segments):
        return Analysis("no_text", "the email has no subject or body text")
    return Analysis("analyzed", segments=segments)


def _html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style).*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", markup)
    return html.unescape(re.sub(r"<[^>]+>", "", markup))


# --- plain text and table rows ----------------------------------------------------------


def _text(data: bytes, settings: CrawlerSettings) -> Analysis:
    text = data.decode("utf-8").replace("\r\n", "\n")
    limit = settings.analyzers.text.max_chars
    detail = f"cut at {limit} of {len(text)} characters" if len(text) > limit else ""
    text = text[:limit]
    if not text.strip():
        return Analysis("no_text", "the file is empty")
    return Analysis("analyzed", detail, [Segment("text", {"part": "text"}, text)])


def _row(data: bytes) -> Analysis:
    document = json.loads(data)
    key = document.get("row_key", {})
    segments = [
        Segment(
            "row_column",
            {"column": column},
            str(value),
            {"row_key": key, "table": document.get("table")},
        )
        for column, value in document.get("text", {}).items()
        if value is not None and str(value).strip()
    ]
    if not segments:
        return Analysis("no_text", "every text column is empty")
    return Analysis("analyzed", segments=segments)


# --- chat --------------------------------------------------------------------------------


def _chat(data: bytes, settings: CrawlerSettings) -> Analysis:
    thread = json.loads(data)
    schema = thread.get("schema")
    if schema not in settings.analyzers.chat.schemas:
        return Analysis("invalid", f"chat schema {schema!r} is not accepted by the settings")
    roles = {p.get("sender"): p.get("role") for p in thread.get("participants", [])}
    segments = []
    for message in thread.get("messages", []):
        message_id = message.get("message_id")
        text = message.get("text")
        if not message_id or not isinstance(text, str):
            return Analysis("invalid", "a chat message lacks message_id or text")
        segments.append(
            Segment(
                "message",
                {"message_id": message_id},
                text,
                {
                    "sender": message.get("sender"),
                    "sender_name": message.get("sender_name"),
                    "role": roles.get(message.get("sender")),
                    "timestamp": message.get("timestamp"),
                    "thread_id": thread.get("thread_id"),
                },
            )
        )
    if not segments:
        return Analysis("no_text", "the chat thread has no messages")
    return Analysis("analyzed", segments=segments)


# --- PDF -----------------------------------------------------------------------------------

_VALUE_LIKE = re.compile(r"[\d$@]|^[A-P]{16}$")


def _pdf(data: bytes, settings: CrawlerSettings) -> Analysis:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    limit = settings.analyzers.pdf.max_pages
    labels = {rule.label.lower(): rule for rule in settings.pdf_labels}
    segments = []
    for number, page in enumerate(reader.pages[:limit], start=1):
        text = page.extract_text() or ""
        segments.append(Segment("page", {"page": number}, text, _pdf_fields(text, labels)))
    if not any(s.text.strip() for s in segments):
        return Analysis("no_text", "no page has a text layer (a scanned PDF needs OCR)")
    detail = f"read {limit} of {len(reader.pages)} pages" if len(reader.pages) > limit else ""
    return Analysis("analyzed", detail, segments)


def _is_header_like(line: str) -> bool:
    words = line.split()
    return 0 < len(words) <= 3 and line[:1].isupper() and not _VALUE_LIKE.search(line)


def _pdf_fields(text: str, labels: dict[str, Any]) -> dict[str, Any]:
    """Label/value pairs and table rows recognised on one page."""
    lines = [line.strip() for line in text.split("\n")]
    used: set[int] = set()
    tables = []
    i = 0
    while i < len(lines):
        # A table header starts at a known label and continues through header-like
        # lines; the same number of lines after it are one row of values.
        if lines[i].lower() in labels:
            end = i
            while end + 1 < len(lines) and _is_header_like(lines[end + 1]):
                end += 1
            width = end - i + 1
            known = sum(1 for line in lines[i : end + 1] if line.lower() in labels)
            if width >= 4 and known >= 2 and end + width < len(lines):
                columns = lines[i : end + 1]
                values = lines[end + 1 : end + 1 + width]
                tables.append(
                    {
                        "columns": columns,
                        "rows": [values],
                        "first_line": i,
                        "last_line": end + width,
                    }
                )
                used.update(range(i, end + 1 + width))
                i = end + 1 + width
                continue
        i += 1
    pairs = []
    for i, line in enumerate(lines[:-1]):
        if i in used or i + 1 in used:
            continue
        rule = labels.get(line.lower())
        value = lines[i + 1]
        if rule is None or not value or value.lower() in labels:
            continue
        pairs.append(
            {
                "label": rule.label,
                "value": value,
                "kind": rule.kind,
                "proposed_class": rule.proposed_class,
                "columns": list(rule.columns),
                "line": i + 1,
            }
        )
    fields: dict[str, Any] = {}
    if pairs:
        fields["labels"] = pairs
    if tables:
        fields["tables"] = tables
    return fields
