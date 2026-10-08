"""Small real assets for crawler tests, shaped like the Helios-DS corpus."""

import io
import json

REPORT_LINES = [
    "Helios Retail",
    "Return Authorization Report",
    "Mrs. Raymond brought the medium tables item back to the Midway store; details below.",
    "RMA number",
    "RMA-6326426",
    "Store",
    "ese (#AAAAAAAAEAAAAAAA), Midway, TN",
    "Original receipt ticket",
    "166147",
    "Returned merchandise",
    "Item ID",
    "Description",
    "Brand",
    "Category",
    "Qty",
    "AAAAAAAAFCOBAAAA",
    "ationoughtationation",
    "scholarnameless #8",
    "Home / tables",
    "64",
    "Customer",
    "Name",
    "Angela Raymond",
    "Customer ID",
    "AAAAAAAADPDEAAAA",
    "Outer packaging crushed on two corners.",
]


def pdf_bytes(lines=REPORT_LINES) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter, invariant=1)
    y = 750
    for line in lines:
        if y < 72:  # start a new page
            pdf.showPage()
            y = 750
        pdf.drawString(72, y, line)
        y -= 18
    pdf.save()
    return buffer.getvalue()


def email_bytes() -> bytes:
    from email.message import EmailMessage
    from email.policy import SMTP

    message = EmailMessage(policy=SMTP)
    message["From"] = "Wilma Graham <Wilma.Graham@t.edu>"
    message["To"] = "care@helios-retail.example"
    message["Subject"] = "Problem with my ableeseantiantiought (ticket 205079)"
    message["Date"] = "Tue, 04 May 1999 09:08:31 +0000"
    message.set_content(
        "Hello,\n\nBox arrived torn; the product scratched.\n\nReturn # RMA-3056773.\n",
        charset="utf-8",
        cte="quoted-printable",
    )
    return message.as_bytes()


def chat_bytes(schema: str = "helios-ds/chat-thread/1.0") -> bytes:
    return json.dumps(
        {
            "schema": schema,
            "thread_id": "thread-1",
            "channel": "support",
            "participants": [
                {"sender": "agent_1", "name": "Rosa D.", "role": "agent"},
                {"sender": "agent_2", "name": "Arjun V.", "role": "inspector"},
            ],
            "messages": [
                {
                    "message_id": "msg-1",
                    "timestamp": "2001-06-16T10:00:00Z",
                    "sender": "agent_1",
                    "sender_name": "Rosa D.",
                    "text": "Got an email about return RMA-6326426.",
                },
                {
                    "message_id": "msg-2",
                    "timestamp": "2001-06-16T10:03:00Z",
                    "sender": "agent_2",
                    "sender_name": "Arjun V.",
                    "text": "Box was crushed, the item has dents.",
                },
            ],
        }
    ).encode()


# The rules the sample documents were written for: the shipped "retail returns"
# preset (until settings schema 2, the crawler's built-in defaults).
from helios_core.crawler.settings import load_preset  # noqa: E402

RETAIL_SETTINGS = load_preset("retail-returns")


COVERAGE_COUNTS = {
    "segments_without_mentions",
    "assets_without_links",
    "unmatched_identifiers",
    "unknown_labels",
    "cases_unresolved",
}


def found(counts: dict) -> dict:
    """A run's counts without the coverage signals (CG-10): what it listed and found."""
    return {
        k: v for k, v in counts.items() if k not in COVERAGE_COUNTS and not k.startswith("claims_dropped_")
    }
