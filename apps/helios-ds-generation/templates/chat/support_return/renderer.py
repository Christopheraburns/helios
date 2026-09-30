"""Internal support chat thread about a damaged-item return.

Written in the Helios-DS neutral chat schema (spec: "Chat -> canonical
JSON/JSONL"), not as a byte-perfect Slack/Teams clone. Message and thread IDs
derive from the artifact ID; timestamps from the TPC-DS return date, the case's
chat delay and seeded gaps between messages.
"""

import datetime as dt
import json

from helios_ds.ids import hash_parts
from helios_ds.render.base import (
    RenderContext,
    RenderedArtifact,
    TextBuilder,
    at_time,
    compose,
    iso,
    parse_date,
    pick,
)
from helios_ds.render.stories import product_return_damage

SCHEMA = "helios-ds/chat-thread/1.0"


def _handle(name: str) -> str:
    """A stable user handle for a staff member (the same across all datasets)."""
    return f"agent_{int(hash_parts('staff', name)[:8], 16) % 10000:04d}"


def render(ctx: RenderContext) -> RenderedArtifact:
    story = product_return_damage(ctx)
    rng = ctx.rng
    params = ctx.rendering_parameters
    staff = {
        "agent": ctx.case.agent_name,
        "supervisor": ctx.case.supervisor_name,
        "inspector": ctx.case.inspector_name,
    }

    turns = list(pick(rng, ctx.phrases["flows"]))
    target = rng.randint(int(params.get("min_messages", 4)), int(params.get("max_messages", 8)))
    fillers = list(ctx.phrases["fillers"])
    while len(turns) < target and fillers:
        filler = fillers.pop(rng.randrange(len(fillers)))
        turns.insert(rng.randrange(1, len(turns) + 1), filler)

    day = parse_date(ctx.fact("return_date")) + dt.timedelta(days=ctx.case.chat_delay_days)
    moment = at_time(day, rng, 9, 17)
    thread_id = f"thread-{ctx.artifact.artifact_id[:8]}"
    messages, mentions = [], []
    for index, (role, text) in enumerate(turns):
        if index:
            moment += dt.timedelta(seconds=rng.randrange(20, 600))
        message_id = f"msg-{hash_parts(ctx.artifact.artifact_id, index)[:12]}"
        builder = compose(TextBuilder(), text, story.values, story.mentions)
        messages.append(
            {
                "message_id": message_id,
                "timestamp": iso(moment),
                "sender": _handle(staff[role]),
                "sender_name": staff[role],
                "text": builder.text(),
            }
        )
        mentions += builder.mentions(message_id=message_id)

    thread = {
        "schema": SCHEMA,
        "thread_id": thread_id,
        "channel": params.get("channel", "support"),
        "participants": [
            {"sender": _handle(name), "name": name, "role": role}
            for role, name in staff.items()
            if any(t[0] == role for t in turns)
        ],
        "messages": messages,
    }
    data = (json.dumps(thread, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return RenderedArtifact(
        data=data,
        mime_type="application/json",
        extension="json",
        semantic_timestamp=messages[0]["timestamp"],
        mentions=mentions,
    )
