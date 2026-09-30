"""Store return authorization report (single-page PDF, ReportLab).

Deterministic by construction: ReportLab's ``invariant`` mode fixes the
creation date and document ID, fonts are the built-in Helvetica family (no
embedded font files), metadata is derived from the scenario, and all layout
variation comes from the artifact's seeded RNG.

Ground-truth locators use the "pdf_page_text_span" strategy: page number plus
the exact text placed on that page.
"""

import datetime as dt
import io
from typing import List, Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from helios_ds.render.base import (
    COMPANY,
    Evidence,
    Mention,
    RenderContext,
    RenderedArtifact,
    TextBuilder,
    compose,
    iso,
    parse_date,
    pick,
)
from helios_ds.render.stories import product_return_damage

ACCENTS = ("#1f4e79", "#7a1f2b", "#2f5d3a", "#4b3f72")


def render(ctx: RenderContext) -> RenderedArtifact:
    story = product_return_damage(ctx)
    values, specs = story.values, story.mentions
    rng = ctx.rng
    accent = colors.HexColor(pick(rng, ACCENTS))
    mentions: List[Mention] = []
    evidence: List[Evidence] = []

    def placed(slot: str) -> str:
        """Place a mention's surface form in a table cell and record it."""
        spec = specs[slot]
        if spec.surface and spec.source_key is not None:
            mentions.append(
                Mention(
                    spec.entity_type,
                    spec.source_key,
                    spec.surface,
                    {"page": 1, "text": spec.surface},
                    spec.tier,
                )
            )
        return spec.surface

    def paragraph(text: str, style: ParagraphStyle, claim: Optional[str] = None) -> Paragraph:
        builder = compose(TextBuilder(), text, values, specs)
        for _, _, surface, entity_type, key, tier in builder.spans:
            mentions.append(Mention(entity_type, key, surface, {"page": 1, "text": surface}, tier))
        if claim:
            evidence.append(Evidence(claim, builder.text(), {"page": 1, "text": builder.text()}))
        return Paragraph(escape(builder.text()), style)

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], textColor=accent, alignment=0, fontSize=18)
    h2 = ParagraphStyle("h2", parent=styles["Heading3"], textColor=accent, spaceBefore=10)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10, leading=13)
    small = ParagraphStyle("small", parent=body, fontSize=8, textColor=colors.grey)

    def grid(rows: List[List[str]], widths: List[float], header: bool = False) -> Table:
        table = Table(rows, colWidths=widths)
        style = [
            ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
        if header:
            style += [
                ("BACKGROUND", (0, 0), (-1, 0), accent),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
            ]
        else:
            style += [("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9)]
        table.setStyle(TableStyle(style))
        return table

    title = pick(rng, ctx.phrases["title"])
    story_flow = [
        Paragraph(escape(COMPANY), small),
        Paragraph(escape(title), h1),
        paragraph(pick(rng, ctx.phrases["intro"]), body),
        Spacer(1, 8),
        grid(
            [
                ["RMA number", placed("rma")],
                ["Support case", placed("case")],
                ["Store", f"{placed('store')} (#{placed('store_id')}), {values['store_location']}"],
                ["Return date", values["return_date_long"]],
                ["Original receipt ticket", placed("ticket")],
                ["Sale date", values["sale_date_long"] or "not on file"],
            ],
            [1.8 * inch, 4.9 * inch],
        ),
    ]
    customer_section = [
        Paragraph("Customer", h2),
        grid(
            [
                ["Name", placed("customer")],
                ["Customer ID", placed("customer_id")],
                ["Email", placed("customer_email")],
            ],
            [1.8 * inch, 4.9 * inch],
        ),
    ]
    item_section = [
        Paragraph("Returned merchandise", h2),
        grid(
            [
                ["Item ID", "Description", "Brand", "Category", "Qty", "Unit price", "Refund"],
                [
                    placed("item_id"),
                    placed("item"),
                    placed("brand"),
                    values["category"],
                    values["quantity"],
                    values["unit_price"],
                    values["refund"],
                ],
            ],
            [1.35 * inch, 1.35 * inch, 1.0 * inch, 1.2 * inch, 0.4 * inch, 0.7 * inch, 0.7 * inch],
            header=True,
        ),
    ]
    sections = [customer_section, item_section]
    if rng.random() < 0.5:
        sections.reverse()
    for section in sections:
        story_flow += section
    story_flow += [
        Paragraph("Inspection", h2),
        paragraph(pick(rng, ctx.phrases["inspection"]), body, "PACKAGING_DAMAGED"),
        paragraph(pick(rng, ctx.phrases["reason_line"]), body, "RETURN_REASON"),
        paragraph(pick(rng, ctx.phrases["disposition"]), body, "REFUND_APPROVED"),
        Spacer(1, 16),
        Paragraph(
            escape(
                f"Inspected by {ctx.case.inspector_name}, returns desk, "
                f"{values['return_date_long']}."
            ),
            body,
        ),
        Spacer(1, 24),
        Paragraph(escape(pick(rng, ctx.phrases["footer"])), small),
    ]

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        title=f"{title} {ctx.case.rma_number}",
        author=f"{COMPANY} Returns Desk",
        subject=f"Return {ctx.case.rma_number}",
        creator="helios-ds return_report",
        invariant=1,
        leftMargin=0.8 * inch,
        rightMargin=0.8 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
    )
    doc.build(story_flow)
    return_day = parse_date(ctx.fact("return_date"))
    return RenderedArtifact(
        data=buffer.getvalue(),
        mime_type="application/pdf",
        extension="pdf",
        semantic_timestamp=iso(
            dt.datetime.combine(return_day, dt.time(17, 0), tzinfo=dt.timezone.utc)
        ),
        mentions=mentions,
        evidence=evidence,
    )
