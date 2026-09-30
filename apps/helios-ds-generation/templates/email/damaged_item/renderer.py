"""Customer email about a damaged product return (.eml, RFC 5322).

Deterministic by construction: single-part text/plain (no MIME boundary), a
Message-ID derived from the artifact ID, a Date derived from the TPC-DS return
date plus a seeded delay and time, and no library-generated values.
"""

import datetime as dt
import email.policy
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime

from helios_ds.render.base import (
    COMPANY,
    SUPPORT_DOMAIN,
    Mention,
    RenderContext,
    RenderedArtifact,
    TextBuilder,
    at_time,
    compose,
    compose_claim,
    iso,
    parse_date,
    pick,
)
from helios_ds.render.stories import product_return_damage


def render(ctx: RenderContext) -> RenderedArtifact:
    story = product_return_damage(ctx)
    values, mentions = story.values, story.mentions
    rng = ctx.rng

    day = parse_date(ctx.fact("return_date")) + dt.timedelta(days=ctx.case.email_delay_days)
    sent = at_time(day, rng)

    subject = compose(TextBuilder(), pick(rng, ctx.phrases["subject"]), values, mentions)

    tone_name = pick(rng, sorted(ctx.phrases["tones"]))
    tone = ctx.phrases["tones"][tone_name]
    body = TextBuilder()
    compose(body, pick(rng, ctx.phrases["greeting"]), values, mentions).add("\n\n")
    compose(body, pick(rng, tone["opening"]), values, mentions).add(" ")
    compose_claim(body, "PACKAGING_DAMAGED", pick(rng, tone["damage"]), values, mentions)
    note = pick(rng, ctx.phrases["reason_note"])
    if note:
        compose_claim(body.add(" "), "RETURN_REASON", note, values, mentions)
    body.add("\n\n")
    compose_claim(body, "REFUND_REQUESTED", pick(rng, tone["request"]), values, mentions)
    body.add("\n\n")
    compose(body, pick(rng, tone["closing"]), values, mentions).add("\n")
    compose(body, pick(rng, ctx.phrases["signoff"]), values, mentions).add("\n")

    local, _, domain = values["customer_email"].partition("@")
    msg = EmailMessage(policy=email.policy.SMTP)
    msg["From"] = Address(display_name=values["customer_name"], username=local, domain=domain)
    msg["To"] = Address(
        display_name=f"{COMPANY} Customer Care", username="care", domain=SUPPORT_DOMAIN
    )
    msg["Subject"] = subject.text()
    msg["Date"] = format_datetime(sent)
    msg["Message-ID"] = f"<{ctx.artifact.artifact_id}@mail.{SUPPORT_DOMAIN}>"
    msg.set_content(body.text(), charset="utf-8", cte="quoted-printable")

    header_mentions = [
        Mention(
            "Customer",
            mentions["customer"].source_key or {},
            values["customer_name"],
            {"header": "From"},
        )
    ]
    return RenderedArtifact(
        data=msg.as_bytes(),
        mime_type="message/rfc822",
        extension="eml",
        semantic_timestamp=iso(sent),
        mentions=header_mentions
        + subject.mentions(part="subject")
        # Offsets count characters of the decoded body with line endings
        # normalised to "\n" (the transport form uses CRLF).
        + body.mentions(part="body", line_endings="LF"),
        evidence=body.evidence(part="body", line_endings="LF"),
    )
