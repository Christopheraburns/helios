"""Per-scenario display values and entity mentions shared by a story's renderers.

Each ``Story`` turns a scenario's TPC-DS facts into
- ``values``: display strings for ``{name}`` placeholders (dates, amounts, names), and
- ``mentions``: ``{@name}`` slots, each tied to the TPC-DS entity it refers to,
so every renderer of the story names the same things the same way.
"""

from dataclasses import dataclass
from typing import Any, Dict, Tuple

from .base import SUPPORT_DOMAIN, MentionSpec, RenderContext, entity_key, long_date, money


@dataclass(frozen=True)
class ClaimSpec:
    subject_table: str  # source_refs table of the subject entity
    object_table: str
    statement: str  # plain-language form, filled from story values


@dataclass
class Story:
    values: Dict[str, Any]
    mentions: Dict[str, MentionSpec]
    # Canonical display name per TPC-DS table (entity) in this story.
    canonical_names: Dict[str, str]
    claims: Dict[str, ClaimSpec]
    # (from table, predicate, to table) business edges between the story's entities.
    relationships: Tuple[Tuple[str, str, str], ...] = ()
    primary_table: str = ""  # the entity the story is about (artifacts DISCUSS it)

    def statement(self, claim_type: str) -> str:
        return self.claims[claim_type].statement.format_map(self.values)


# TPC-DS table -> ground-truth entity type.
ENTITY_TYPES = {
    "customer": "Customer",
    "item": "Item",
    "store": "Store",
    "store_returns": "Return",
    "store_sales": "Sale",
    "reason": "Reason",
    "web_returns": "Return",
    "web_page": "WebPage",
    "warehouse": "Warehouse",
    "inventory": "InventoryRecord",
    "promotion": "Promotion",
}


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
    ticket = ctx.fact("sr_ticket_number")
    color, item_class = ctx.fact("i_color"), ctx.fact("i_class").lower()
    salutation, last_name = ctx.fact("c_salutation"), ctx.fact("c_last_name")
    D, A, C = "direct", "alias", "contextual"
    mentions: Dict[str, MentionSpec] = {
        # direct: canonical names and official identifiers
        "customer": MentionSpec(name, "Customer", customer, D),
        "first_name": MentionSpec(values["first_name"], "Customer", customer, D),
        "customer_id": MentionSpec(ctx.fact("c_customer_id"), "Customer", customer, D),
        "item": MentionSpec(ctx.fact("i_product_name"), "Item", item, D),
        "item_id": MentionSpec(ctx.fact("i_item_id"), "Item", item, D),
        "brand": MentionSpec(
            ctx.fact("i_brand"),
            "Brand",
            {"table": "item", "i_brand": ctx.fact("i_brand")} if ctx.fact("i_brand") else None,
            D,
        ),
        "store": MentionSpec(ctx.fact("s_store_name"), "Store", store, D),
        "store_id": MentionSpec(ctx.fact("s_store_id"), "Store", store, D),
        "ticket": MentionSpec(ticket, "Sale", sale or ret, D),
        "rma": MentionSpec(ctx.case.rma_number, "Return", ret, D),
        "reason": MentionSpec(ctx.fact("r_reason_desc"), "Reason", reason, D),
        # alias: other names that identify the entity together with structured data
        # e.g. "yellow kids item": colour and class, resolvable through the item table
        "item_desc": MentionSpec(
            " ".join(p for p in (color, item_class, "item") if p)
            if color or item_class
            else "order",
            "Item",
            item,
            A,
        ),
        "store_city": MentionSpec(
            f"{ctx.fact('s_city')} store" if ctx.fact("s_city") else "local store",
            "Store",
            store,
            A,
        ),
        "customer_formal": MentionSpec(
            f"{salutation} {last_name}" if salutation and last_name else email,
            "Customer",
            customer,
            A,
        ),
        "customer_email": MentionSpec(email, "Customer", customer, A),
        "receipt_tail": MentionSpec(
            f"receipt ending in {ticket[-4:]}" if ticket else "original receipt",
            "Sale",
            sale or ret,
            A,
        ),
        "case": MentionSpec(ctx.case.case_number, "Return", ret, A),
        # contextual: resolvable only from the surrounding text
        "item_ref": MentionSpec("the item", "Item", item, C),
        "product_ref": MentionSpec("the product", "Item", item, C),
        "store_ref": MentionSpec("that store", "Store", store, C),
        "customer_ref": MentionSpec("the customer", "Customer", customer, C),
        "return_ref": MentionSpec("this return", "Return", ret, C),
    }
    canonical_names = {
        "customer": f"{name} ({ctx.fact('c_customer_id')})",
        "item": f"{ctx.fact('i_product_name')} ({ctx.fact('i_item_id')})",
        "store": f"{ctx.fact('s_store_name')} ({ctx.fact('s_store_id')})",
        "store_returns": (
            f"Return of item {ctx.fact('i_item_id')} on ticket {ctx.fact('sr_ticket_number')}"
        ),
        "store_sales": "Sale ticket "
        + (ctx.fact("ss_ticket_number") or ctx.fact("sr_ticket_number")),
        "reason": ctx.fact("r_reason_desc"),
    }
    claims = {
        "PACKAGING_DAMAGED": ClaimSpec(
            "item",
            "store_returns",
            "The packaging of {i_product_name} ({i_item_id}) was damaged (return {rma_number}).",
        ),
        "RETURN_REASON": ClaimSpec(
            "store_returns", "reason", "Return {rma_number} was recorded as: {r_reason_desc}."
        ),
        "REFUND_REQUESTED": ClaimSpec(
            "customer",
            "store_returns",
            "{customer_name} requested a refund for return {rma_number}.",
        ),
        "REFUND_APPROVED": ClaimSpec(
            "store_returns",
            "customer",
            "A refund of {refund} was approved for return {rma_number}.",
        ),
    }
    relationships = (
        ("customer", "PURCHASED_IN", "store_sales"),
        ("store_sales", "CONTAINS", "item"),
        ("store_sales", "OCCURRED_AT", "store"),
        ("store_returns", "REFERS_TO_SALE", "store_sales"),
        ("store_returns", "RETURNS_ITEM", "item"),
        ("store_returns", "HAS_REASON", "reason"),
        ("store_returns", "RETURNED_BY", "customer"),
        ("store_returns", "RETURNED_AT", "store"),
    )
    return Story(values, mentions, canonical_names, claims, relationships, "store_returns")
