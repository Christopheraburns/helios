"""Shared toolkit for deterministic artifact renderers.

Renderers live in their template directories (``templates/<type>/<id>/renderer.py``,
with phrase banks in ``phrases.yaml``) so the template content hash covers them.
This module holds what they share; changing it changes the generator version.

Rules every renderer follows:
- All facts come from the scenario plan (TPC-DS); nothing contradicts them.
- All variation comes from the artifact's seeded RNG (``ctx.rng``) or the
  scenario's case RNG (values shared by every artifact of one story).
- No wall-clock time, randomness from libraries, or environment-dependent values
  reach the bytes.
- Every placed entity mention is recorded with an exact locator, for ground truth.
"""

import datetime as dt
import random
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, TypeVar

from ..ids import hash_parts, rng_for
from ..scenarios import ArtifactPlan, ScenarioPlan

T = TypeVar("T")

COMPANY = "Helios Retail"
SUPPORT_DOMAIN = "helios-retail.example"  # .example is reserved: never a real domain


@dataclass(frozen=True)
class Mention:
    """An entity mention placed in an artifact (ground truth for task C-04)."""

    entity_type: str  # Customer, Item, Store, Return, Sale, Reason, ...
    source_key: Dict[str, Any]  # TPC-DS table + key, e.g. {"table": "item", "i_item_sk": 939}
    surface_form: str
    locator: Dict[str, Any]  # page / message / char offsets, per template strategy


@dataclass
class RenderedArtifact:
    data: bytes
    mime_type: str
    extension: str
    semantic_timestamp: str  # ISO 8601, derived from TPC-DS dates
    mentions: List[Mention] = field(default_factory=list)


def pick(rng: random.Random, options: Sequence[T]) -> T:
    if not options:
        raise ValueError("nothing to pick from")
    return options[rng.randrange(len(options))]


def fill(text: str, values: Mapping[str, Any]) -> str:
    return text.format_map(dict(values))


def parse_date(value: Any) -> dt.date:
    return dt.date.fromisoformat(str(value)[:10])


def at_time(
    day: dt.date, rng: random.Random, start_hour: int = 8, end_hour: int = 20
) -> dt.datetime:
    """A seeded time of day on ``day`` (UTC)."""
    seconds = rng.randrange(start_hour * 3600, end_hour * 3600)
    return dt.datetime.combine(day, dt.time(), tzinfo=dt.timezone.utc) + dt.timedelta(
        seconds=seconds
    )


def iso(moment: dt.datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Case:
    """Values shared by every artifact of one scenario, derived from its seed,
    so the report, email and chat of one story agree with each other."""

    rma_number: str
    case_number: str
    agent_name: str
    inspector_name: str
    supervisor_name: str
    email_delay_days: int  # complaint email: days after the return
    chat_delay_days: int  # internal chat: days after the return (after the email)

    @classmethod
    def for_scenario(cls, scenario: ScenarioPlan) -> "Case":
        rng = rng_for(hash_parts(scenario.scenario_seed, "case"))
        email_delay = rng.randrange(0, 3)
        rma, case = rng.randrange(10**7), rng.randrange(10**6)
        agent, inspector, supervisor = rng.sample(STAFF_NAMES, 3)  # three different people
        return cls(
            rma_number=f"RMA-{rma:07d}",
            case_number=f"CS-{case:06d}",
            agent_name=agent,
            inspector_name=inspector,
            supervisor_name=supervisor,
            email_delay_days=email_delay,
            chat_delay_days=email_delay + 1 + rng.randrange(0, 2),
        )


# Fictional staff first-name + surname-initial pairs (not drawn from TPC-DS
# customers, so staff never collide with customer entities).
STAFF_NAMES = (
    "Avery M.",
    "Jordan K.",
    "Priya S.",
    "Mateo R.",
    "Hannah L.",
    "Tomas B.",
    "Keiko T.",
    "Samuel O.",
    "Nadia F.",
    "Owen P.",
    "Lucia G.",
    "Daniel W.",
    "Imani C.",
    "Felix H.",
    "Rosa D.",
    "Victor N.",
    "Grace Y.",
    "Arjun V.",
)


@dataclass
class RenderContext:
    scenario: ScenarioPlan
    artifact: ArtifactPlan
    phrases: Dict[str, Any]
    rendering_parameters: Dict[str, Any]
    case: Case
    rng: random.Random

    @property
    def facts(self) -> Dict[str, Any]:
        return self.scenario.facts

    def fact(self, name: str, default: str = "") -> str:
        value = self.scenario.facts.get(name)
        return default if value is None else str(value)

    def phrase(self, key: str, **values: Any) -> str:
        """A seeded choice from the phrase bank, with {placeholders} filled."""
        return fill(pick(self.rng, self.phrases[key]), {**self.values(), **values})

    def values(self) -> Dict[str, Any]:
        """Facts plus case values, for filling phrase templates."""
        return {
            **{k: ("" if v is None else v) for k, v in self.scenario.facts.items()},
            "rma_number": self.case.rma_number,
            "case_number": self.case.case_number,
            "agent_name": self.case.agent_name,
            "company": COMPANY,
        }


class TextBuilder:
    """Builds text while recording where each entity mention lands."""

    def __init__(self) -> None:
        self._parts: List[str] = []
        self._length = 0
        self.spans: List[Tuple[int, int, str, str, Dict[str, Any]]] = []

    def add(self, text: str) -> "TextBuilder":
        self._parts.append(text)
        self._length += len(text)
        return self

    def mention(self, text: str, entity_type: str, source_key: Dict[str, Any]) -> "TextBuilder":
        start = self._length
        self.add(text)
        self.spans.append((start, self._length, text, entity_type, source_key))
        return self

    def text(self) -> str:
        return "".join(self._parts)

    def mentions(self, **locator: Any) -> List[Mention]:
        return [
            Mention(entity_type, key, surface, {**locator, "start": start, "end": end})
            for start, end, surface, entity_type, key in self.spans
        ]


MentionSpec = Tuple[str, str, Optional[Dict[str, Any]]]  # (surface, entity_type, source_key)

_SLOT = re.compile(r"\{@([a-z_]+)\}")


def compose(
    builder: "TextBuilder",
    text: str,
    values: Mapping[str, Any],
    mentions: Mapping[str, MentionSpec],
) -> "TextBuilder":
    """Append a phrase: ``{name}`` fills a value, ``{@name}`` places a recorded
    entity mention (dropped silently only if its surface form is empty)."""
    position = 0
    for match in _SLOT.finditer(text):
        builder.add(fill(text[position : match.start()], values))
        surface, entity_type, key = mentions[match.group(1)]
        if surface:
            if key is None:
                builder.add(surface)
            else:
                builder.mention(surface, entity_type, key)
        position = match.end()
    builder.add(fill(text[position:], values))
    return builder


def long_date(value: Any) -> str:
    day = parse_date(value)
    return f"{day:%B} {day.day}, {day.year}"


def money(value: Any) -> str:
    return f"${float(value):,.2f}"


def entity_key(scenario: ScenarioPlan, table: str) -> Optional[Dict[str, Any]]:
    """The source_refs entry for ``table`` as a ground-truth key, if present."""
    for ref in scenario.source_refs:
        if ref["table"] == table:
            return {"table": table, **ref["key"]}
    return None
