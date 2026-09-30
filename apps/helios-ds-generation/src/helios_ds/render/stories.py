"""Per-scenario display values and entity mentions shared by a story's renderers.

Each ``Story`` turns a scenario's TPC-DS facts into
- ``values``: display strings for ``{name}`` placeholders (dates, amounts, names), and
- ``mentions``: ``{@name}`` slots, each tied to the TPC-DS entity it refers to,
so every renderer of the story names the same things the same way.
"""

from dataclasses import dataclass
from typing import Any, Dict

from .base import SUPPORT_DOMAIN, MentionSpec, RenderContext, entity_key, long_date, money


@dataclass
class Story:
    values: Dict[str, Any]
    mentions: Dict[str, MentionSpec]


def article(word: str) -> str:
    """ "an" before a vowel sound (by first letter), otherwise "a"."""
    return "an" if word[:1].lower() in "aeiou" and word else "a"


def _customer_name(ctx: RenderContext) -> str:
    name = " ".join(p for p in (ctx.fact("c_first_name"), ctx.fact("c_last_name")) if p)
    return name or f"Customer {ctx.fact('c_customer_id')}"


def product_return_damage(ctx: RenderContext) -> Story:
    s = ctx.scenario
    customer = entity_key(s, "customer")
    item = entity_key(s, "item")
    store = entity_key(s, "store")
    ret = entity_key(s, "store_returns")
    sale = entity_key(s, "store_sales")
    reason = entity_key(s, "reason")
    name = _customer_name(ctx)
    email = ctx.fact("c_email_address") or (
        f"{ctx.fact('c_customer_id').lower()}@customers.{SUPPORT_DOMAIN}"
    )
    values = {
        **ctx.values(),
        "customer_name": name,
        "first_name": ctx.fact("c_first_name") or name,
        "customer_email": email,
        "return_date_long": long_date(ctx.fact("return_date")),
        "sale_date_long": long_date(ctx.fact("sale_date")) if ctx.fact("sale_date") else "",
        "refund": money(ctx.fact("sr_return_amt") or 0),
        "unit_price": money(ctx.fact("ss_sales_price") or 0),
        "quantity": ctx.fact("sr_return_quantity", "1"),
        "store_location": f"{ctx.fact('s_city')}, {ctx.fact('s_state')}",
        "a_item": article(ctx.fact("i_product_name")),
        "category": f"{ctx.fact('i_category')} / {ctx.fact('i_class')}",
    }
    mentions: Dict[str, MentionSpec] = {
        "customer": (name, "Customer", customer),
        "first_name": (values["first_name"], "Customer", customer),
        "customer_id": (ctx.fact("c_customer_id"), "Customer", customer),
        "item": (ctx.fact("i_product_name"), "Item", item),
        "item_id": (ctx.fact("i_item_id"), "Item", item),
        "brand": (ctx.fact("i_brand"), "Brand", item),
        "store": (ctx.fact("s_store_name"), "Store", store),
        "store_id": (ctx.fact("s_store_id"), "Store", store),
        "ticket": (ctx.fact("sr_ticket_number"), "Sale", sale or ret),
        "rma": (ctx.case.rma_number, "Return", ret),
        "reason": (ctx.fact("r_reason_desc"), "Reason", reason),
    }
    return Story(values, mentions)
