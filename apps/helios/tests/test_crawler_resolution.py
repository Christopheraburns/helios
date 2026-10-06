"""CR-5: entity resolution on real-shaped assets against a DuckDB stand-in for
the TPC-DS warehouse: candidates per tier, case clusters, joint resolution with
one parameterised query per cluster, contextual references, link decisions,
entities with their external IDs, and derived relationships."""

import hashlib
import json
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
from apps.helios.crawler import cases, resolution
from apps.helios.crawler.analyzers import analyze_asset
from apps.helios.crawler.connectors import SourceAsset
from apps.helios.crawler.crawl import segment_rows
from apps.helios.crawler.gazetteer import Gazetteer
from apps.helios.crawler.mentions import extract_mentions
from apps.helios.crawler.resolution import JointSchema, build_query, gather_constraints, resolve
from crawler_samples import chat_bytes, pdf_bytes
from helios_core.crawler.settings import DEFAULT_SETTINGS
from helios_core.index import ids
from helios_core.index.records import AssetRecord, CrawlRunRecord
from helios_core.ontology.mapping import load_mapping, resolution_config

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG = resolution_config(load_mapping(REPO_ROOT / "ontology/mappings/ossie/tpcds.yaml"))
CLASSES = ["Customer", "Item", "Brand", "Store", "Reason", "Sale", "Return"]

RETURN_A = ids.external_id("tpcds", "store_returns", {"sr_ticket_number": 205079, "sr_item_sk": 11})
SALE_A = ids.external_id("tpcds", "store_sales", {"ss_ticket_number": 205079, "ss_item_sk": 11})
RETURN_B = ids.external_id("tpcds", "store_returns", {"sr_ticket_number": 166147, "sr_item_sk": 10})
CUSTOMER = {n: f"tpcds.customer:c_customer_sk={n}" for n in (1, 2, 3, 5, 6, 7)}
ITEM = {n: f"tpcds.item:i_item_sk={n}" for n in (10, 11, 12)}
STORE = {n: f"tpcds.store:s_store_sk={n}" for n in (4, 8, 9)}
REASON = {n: f"tpcds.reason:r_reason_sk={n}" for n in (1, 2)}
BRAND = {n: f"tpcds.item:i_brand_id={n}" for n in (1, 8)}


# --- the stand-in warehouse -----------------------------------------------------------------


def build_warehouse() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("CREATE SCHEMA tpcds")
    con.execute(
        "CREATE TABLE tpcds.customer (c_customer_sk BIGINT, c_customer_id VARCHAR, "
        "c_email_address VARCHAR, c_first_name VARCHAR, c_last_name VARCHAR, c_salutation VARCHAR)"
    )
    con.executemany(
        "INSERT INTO tpcds.customer VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, "AAAAAAAADPDEAAAA", "Angela.Raymond@7T.edu", "Angela", "Raymond", "Mrs."),
            (2, "AAAAAAAAGLMDAAAB", "Wilma.Graham@t.edu", "Wilma", "Graham", "Dr."),
            (3, "AAAAAAAAGLMDAAAC", None, None, "Smith", "Mr."),
            (5, "AAAAAAAAPRPRAAAA", "Pauline.Raymond@x.org", "Pauline", "Raymond", "Mrs."),
            (6, "AAAAAAAAKEMPAAAA", "Angela.Kemp@x.org", "Angela", "Kemp", "Ms."),
            (7, "AAAAAAAAFELTAAAA", "Ted.Felton@x.org", "Ted", "Felton", "Mr."),
        ],
    )
    con.execute(
        "CREATE TABLE tpcds.store (s_store_sk BIGINT, s_store_id VARCHAR, s_store_name VARCHAR, s_city VARCHAR)"
    )
    con.executemany(
        "INSERT INTO tpcds.store VALUES (?, ?, ?, ?)",
        [
            (4, "AAAAAAAAEAAAAAAA", "ese", "Midway"),
            (8, "AAAAAAAAIAAAAAAA", "eing", "Fairview"),
            (9, "AAAAAAAAIAAAAAAA", "eing", "Midway"),
        ],
    )
    con.execute(
        "CREATE TABLE tpcds.item (i_item_sk BIGINT, i_item_id VARCHAR, i_product_name VARCHAR, "
        "i_color VARCHAR, i_class VARCHAR, i_brand_id BIGINT, i_brand VARCHAR)"
    )
    con.executemany(
        "INSERT INTO tpcds.item VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                10,
                "AAAAAAAAFCOBAAAA",
                "ationoughtationation",
                "medium",
                "tables",
                8,
                "scholarnameless #8",
            ),
            (
                11,
                "AAAAAAAAGLMDAAAA",
                "ableeseantiantiought",
                "blanched",
                "fragrances",
                1,
                "brandcorp #1",
            ),
            # The same business ID again: a slowly changing dimension's second version.
            (
                12,
                "AAAAAAAAGLMDAAAA",
                "ableeseantiantioughtation",
                "blanched",
                "fragrances",
                1,
                "brandcorp #1",
            ),
        ],
    )
    con.execute(
        "CREATE TABLE tpcds.reason (r_reason_sk BIGINT, r_reason_id VARCHAR, r_reason_desc VARCHAR)"
    )
    con.executemany(
        "INSERT INTO tpcds.reason VALUES (?, ?, ?)",
        [
            (1, "AAAAAAAABAAAAAAA", "Package was damaged"),
            (2, "AAAAAAAACAAAAAAA", "Not the product that was ordered"),
        ],
    )
    con.execute("CREATE TABLE tpcds.date_dim (d_date_sk BIGINT, d_date DATE)")
    con.executemany(
        "INSERT INTO tpcds.date_dim VALUES (?, ?)",
        [
            (1, "1999-05-04"),
            (2, "1999-04-20"),
            (3, "2001-06-14"),
            (4, "2001-05-19"),
            (5, "2002-01-10"),
            (6, "2002-02-10"),
        ],
    )
    con.execute(
        "CREATE TABLE tpcds.store_returns (sr_returned_date_sk BIGINT, sr_item_sk BIGINT, sr_customer_sk BIGINT, "
        "sr_store_sk BIGINT, sr_reason_sk BIGINT, sr_ticket_number BIGINT, sr_return_amt DECIMAL(7,2))"
    )
    con.executemany(
        "INSERT INTO tpcds.store_returns VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (1, 11, 2, 9, 1, 205079, 42.00),  # the worked example: Wilma Graham, RMA-3056773
            (1, 12, 5, 4, 1, 305079, 42.00),  # another receipt ending in 5079, another customer
            (3, 10, 1, 4, 1, 166147, 310.40),  # Angela Raymond, RMA-6326426
            (5, 10, 7, 4, 2, 400001, 10.00),  # Ted Felton returned the same item twice...
            (6, 10, 7, 9, 2, 400002, 10.00),  # ...at two Midway stores: ambiguous without a date
        ],
    )
    con.execute(
        "CREATE TABLE tpcds.store_sales (ss_sold_date_sk BIGINT, ss_item_sk BIGINT, ss_customer_sk BIGINT, "
        "ss_store_sk BIGINT, ss_ticket_number BIGINT, ss_net_paid DECIMAL(7,2))"
    )
    con.executemany(
        "INSERT INTO tpcds.store_sales VALUES (?, ?, ?, ?, ?, ?)",
        [
            (2, 11, 2, 8, 205079, 42.00),  # TPC-DS's own sale store differs from the return's
            (2, 12, 5, 4, 305079, 42.00),
            (4, 10, 1, 4, 166147, 310.40),
            (5, 10, 7, 4, 400001, 10.00),
            (6, 10, 7, 9, 400002, 10.00),
        ],
    )
    return con


# --- the documents ----------------------------------------------------------------------------


def email(sender: str, subject: str, body: str) -> bytes:
    message = EmailMessage(policy=SMTP)
    message["From"] = sender
    message["To"] = "care@helios-retail.example"
    message["Subject"] = subject
    message["Date"] = "Tue, 04 May 1999 09:08:31 +0000"
    message.set_content(body, charset="utf-8", cte="quoted-printable")
    return message.as_bytes()


def chat(*texts: str) -> bytes:
    thread = json.loads(chat_bytes())
    thread["messages"] = [
        {**thread["messages"][0], "message_id": f"msg-{i}", "text": text}
        for i, text in enumerate(texts, start=1)
    ]
    return json.dumps(thread).encode()


EMAIL_A = email(
    "Wilma Graham <Wilma.Graham@t.edu>",
    "Problem with my ableeseantiantiought",
    "Re: blanched fragrances item (AAAAAAAAGLMDAAAA), receipt ending in 5079, returned "
    "May 4, 1999. Box arrived torn; the product scratched. The return slip says: "
    "Package was damaged.\n\nReturn # RMA-3056773. When will the refund post?\n\n"
    "Thanks,\nWilma\n",
)
CHAT_A = chat(
    "Got an email about return RMA-3056773 from Wilma.Graham@t.edu; she went to the Midway store.",
    "Mrs. Raymond? No, this is Wilma. The item is the blanched fragrances item; refund of $42.00 approved.",
)
PDF_A = pdf_bytes(
    [
        "Helios Retail",
        "Return Authorization Report",
        "RMA number",
        "RMA-3056773",
        "Support case",
        "CS-111222",
        "Store",
        "eing (#AAAAAAAAIAAAAAAA), Midway, TN",
        "Return date",
        "May 4, 1999",
        "Original receipt ticket",
        "205079",
        "Returned merchandise",
        "Item ID",
        "Description",
        "Brand",
        "Category",
        "AAAAAAAAGLMDAAAA",
        "ableeseantiantiought",
        "brandcorp #1",
        "Home / fragrances",
        "Customer",
        "Name",
        "Wilma Graham",
        "Customer ID",
        "AAAAAAAAGLMDAAAB",
        "Recorded return reason: Package was damaged.",
    ]
)
PDF_B = (
    pdf_bytes()
)  # RMA-6326426: Mrs. Raymond, ese (#AAAAAAAAEAAAAAAA), ticket 166147, item AAAAAAAAFCOBAAAA
CHAT_B = chat_bytes()  # RMA-6326426; "the item has dents"
EMAIL_C = email(
    "Ted Felton <Ted.Felton@x.org>",
    "Returned my ationoughtationation",
    "I brought the ationoughtationation (AAAAAAAAFCOBAAAA) back to the Midway store.\n"
    "Return # RMA-7000001.\n\nThx,\nTed\n",
)
CHAT_D = chat(
    "Angela.Raymond@7T.edu says ticket 999999 was the blanched fragrances item; the item never arrived."
)
ASSETS = {
    "a-email": ("message/rfc822", EMAIL_A),
    "a-chat": ("application/json", CHAT_A),
    "a-pdf": ("application/pdf", PDF_A),
    "b-pdf": ("application/pdf", PDF_B),
    "b-chat": ("application/json", CHAT_B),
    "c-email": ("message/rfc822", EMAIL_C),
    "d-chat": ("application/json", CHAT_D),
}


def _run(run_id="run-1"):
    return CrawlRunRecord(
        crawl_run_id=run_id,
        connector="helios_ds",
        source="ds-1",
        ontology_version="0.2.0",
        crawler_version="0.5.0",
        status="RUNNING",
        started_at="2026-01-01T00:00:00Z",
        recorded_at="2026-01-01T00:00:00Z",
        actor="test",
    )


def _asset_record(run, asset: SourceAsset) -> AssetRecord:
    return AssetRecord(
        crawl_run_id=run.crawl_run_id,
        asset_id=asset.asset_id,
        asset_version_id=asset.asset_version_id,
        connector="helios_ds",
        source="ds-1",
        ontology_class="Document",
        mime_type=asset.mime_type,
        source_locator={},
        size_bytes=1,
        status="analyzed",
    )


def read_assets(run, gazetteer, names=None):
    """(asset records, segments, mentions) of the sample documents."""
    assets, segments, mentions = [], [], []
    for asset_id, (mime, data) in ASSETS.items():
        if names is not None and asset_id not in names:
            continue
        asset = SourceAsset(
            asset_id=asset_id, source="ds-1", mime_type=mime, locator={}, version=f"v-{asset_id}"
        )
        analysis = analyze_asset(mime, data, DEFAULT_SETTINGS)
        assert analysis.status == "analyzed", analysis.detail
        own = segment_rows(run, asset, analysis.segments)
        assets.append(_asset_record(run, asset))
        segments += own
        mentions += extract_mentions(run, asset, own, DEFAULT_SETTINGS, gazetteer, CONFIG)
    return assets, segments, mentions


@pytest.fixture(scope="module")
def warehouse():
    return build_warehouse()


@pytest.fixture(scope="module")
def gazetteer(warehouse):
    return Gazetteer.build(warehouse.cursor, CONFIG, CLASSES)


@pytest.fixture(scope="module")
def world(warehouse, gazetteer):
    run = _run()
    assets, segments, mentions = read_assets(run, gazetteer)
    result = resolve(
        run, assets, segments, mentions, gazetteer, CONFIG, DEFAULT_SETTINGS, warehouse.cursor
    )
    return SimpleNamespace(
        run=run,
        assets=assets,
        segments=segments,
        mentions=mentions,
        result=result,
        entities={e.entity_id: e for e in result.entities},
        links_by_mention=_group(result.links, lambda l: l.mention_id),
    )


def _group(items, key):
    grouped = {}
    for item in items:
        grouped.setdefault(key(item), []).append(item)
    return grouped


def _mention(world, asset_id: str, surface: str, extractor: str | None = None):
    found = [
        m
        for m in world.mentions
        if m.asset_id == asset_id
        and m.surface_form == surface
        and (extractor is None or m.extractor == extractor)
    ]
    assert found, f"no mention {surface!r} in {asset_id}"
    return found[0]


def _links(world, asset_id: str, surface: str, extractor: str | None = None):
    links = world.links_by_mention.get(_mention(world, asset_id, surface, extractor).mention_id, [])
    return sorted(
        (
            (l.link_type, l.resolved_by, l.score, world.entities[l.entity_id].external_ids[0])
            for l in links
        ),
        key=lambda t: t[3],
    )


# --- candidates and link decisions --------------------------------------------------------------


def test_exact_keys_resolve_to_sameas(world):
    assert _links(world, "a-email", "Wilma.Graham@t.edu") == [("SameAs", "joint", 1.0, CUSTOMER[2])]
    assert _links(world, "d-chat", "Angela.Raymond@7T.edu") == [
        ("SameAs", "exact_key", 1.0, CUSTOMER[1])
    ]
    # A business ID shared by two versions of the item is not an exact key on its own.
    assert _links(world, "d-chat", "blanched fragrances item") == [
        ("PossiblySameAs", "alias", 0.5, ITEM[11]),
        ("PossiblySameAs", "alias", 0.5, ITEM[12]),
    ]


def test_a_shared_alias_stays_possibly_same_as(world):
    assert _links(world, "a-chat", "Mrs. Raymond") == [
        ("PossiblySameAs", "alias", 0.5, CUSTOMER[1]),
        ("PossiblySameAs", "alias", 0.5, CUSTOMER[5]),
    ]
    # ... and so does a store name in a cluster the warehouse could not settle.
    assert _links(world, "c-email", "Midway store") == [
        ("PossiblySameAs", "alias", 0.5, STORE[4]),
        ("PossiblySameAs", "alias", 0.5, STORE[9]),
    ]


def test_partial_names_resolve_through_their_anchor(world):
    assert _links(world, "c-email", "Ted") == [("SameAs", "alias", 1.0, CUSTOMER[7])]
    assert _links(world, "a-chat", "Wilma")[0][3] == CUSTOMER[2]


def test_no_sameas_below_its_tier_threshold(world):
    thresholds = {
        "alias": CONFIG.thresholds.alias_min_score,
        "fuzzy": CONFIG.thresholds.fuzzy_min_score,
    }
    assert world.result.links, "no links at all"
    for link in world.result.links:
        if link.link_type == "SameAs":
            assert link.score >= thresholds.get(link.resolved_by, 1.0), link
            assert link.evidence_segment_ids, link
        assert link.link_type in ("SameAs", "PossiblySameAs")
        assert link.resolved_by in ("exact_key", "alias", "fuzzy", "joint", "contextual")


# --- cases and the joint query ---------------------------------------------------------------


def test_documents_sharing_an_rma_form_one_case(world):
    clusters = {tuple(members) for members in world.result.clusters.values()}
    assert clusters == {
        ("a-chat", "a-email", "a-pdf"),
        ("b-chat", "b-pdf"),
        ("c-email",),
        ("d-chat",),
    }
    assert world.result.counts["cases"] == 4
    assert world.result.counts["cases_resolved"] == 2


def test_the_cluster_resolves_jointly_to_one_return_and_promotes_its_aliases(world):
    assert _links(world, "a-chat", "Midway store") == [("SameAs", "joint", 1.0, STORE[9])]
    assert _links(world, "a-pdf", "eing") == [("SameAs", "joint", 1.0, STORE[9])]
    assert _links(world, "a-email", "blanched fragrances item") == [
        ("SameAs", "joint", 1.0, ITEM[11])
    ]
    assert _links(world, "a-email", "AAAAAAAAGLMDAAAA") == [("SameAs", "joint", 1.0, ITEM[11])]
    assert _links(world, "a-email", "5079") == [("SameAs", "joint", 1.0, SALE_A)]
    assert _links(world, "a-pdf", "205079") == [("SameAs", "joint", 1.0, SALE_A)]
    assert _links(world, "a-email", "RMA-3056773") == [("SameAs", "joint", 1.0, RETURN_A)]
    assert _links(world, "a-pdf", "CS-111222") == [("SameAs", "joint", 1.0, RETURN_A)]
    assert _links(world, "b-pdf", "Mrs. Raymond") == [("SameAs", "joint", 1.0, CUSTOMER[1])]
    assert _links(world, "b-pdf", "medium tables item") == [("SameAs", "joint", 1.0, ITEM[10])]
    [link] = world.links_by_mention[_mention(world, "a-chat", "Midway store").mention_id]
    carriers = {
        s.segment_id for s in world.segments if s.asset_id in ("a-email", "a-pdf", "a-chat")
    }
    assert set(link.evidence_segment_ids) <= carriers and len(link.evidence_segment_ids) >= 3


def test_a_receipt_tail_constrains_the_ticket(warehouse, gazetteer):
    run = _run()
    assets, segments, mentions = read_assets(run, gazetteer, names={"a-email"})
    rules = cases.Rules(DEFAULT_SETTINGS)
    reader = resolution.Reader(gazetteer, CONFIG, rules)
    readings = list(resolution.read_all(mentions, reader).values())
    constraints = gather_constraints(readings)
    assert constraints.tails == {"5079"} and not constraints.tickets
    assert constraints.instances["Customer"] == {CUSTOMER[2]}
    assert constraints.instances["Item"] == {ITEM[11], ITEM[12]}
    schema = JointSchema.from_config(CONFIG)
    sql, params = build_query(schema, constraints)
    assert "MOD(r.sr_ticket_number, ?) = ?" in sql
    assert "r.sr_customer_sk IN (?)" in sql and "r.sr_item_sk IN (?, ?)" in sql
    assert (
        "LEFT JOIN tpcds.store_sales s ON s.ss_ticket_number = r.sr_ticket_number AND s.ss_item_sk = r.sr_item_sk"
        in sql
    )
    assert "LEFT JOIN tpcds.date_dim dr ON dr.d_date_sk = r.sr_returned_date_sk" in sql
    assert "LEFT JOIN tpcds.item j0 ON j0.i_item_sk = r.sr_item_sk" in sql  # Brand lives on item
    assert params == [2, 11, 12, 1, 10000, 5079]  # customer, the item's two versions, reason, tail
    assert "RMA" not in sql and "5079" not in sql  # document values are bound, never interpolated
    result = resolve(
        run, assets, segments, mentions, gazetteer, CONFIG, DEFAULT_SETTINGS, warehouse.cursor
    )
    assert result.resolved == 1
    returns = [e for e in result.entities if e.ontology_class == "Return"]
    assert [e.external_ids for e in returns] == [[RETURN_A, "documents.return:rma=RMA-3056773"]]


def test_zero_or_several_rows_promote_nothing(world):
    # Ted's two returns of the same item at two Midway stores: several rows.
    assert not any(
        l.resolved_by == "joint"
        for m in world.mentions
        if m.asset_id == "c-email"
        for l in world.links_by_mention.get(m.mention_id, [])
    )
    assert _links(world, "c-email", "AAAAAAAAFCOBAAAA") == [("SameAs", "exact_key", 1.0, ITEM[10])]
    assert _links(world, "c-email", "RMA-7000001") == [
        ("SameAs", "exact_key", 1.0, "documents.return:rma=RMA-7000001")
    ]
    # Ticket 999999 exists nowhere: zero rows.
    assert not any(
        l.resolved_by == "joint"
        for m in world.mentions
        if m.asset_id == "d-chat"
        for l in world.links_by_mention.get(m.mention_id, [])
    )
    assert world.links_by_mention.get(_mention(world, "d-chat", "999999").mention_id, []) == []


def test_the_query_runs_on_the_warehouse_cursor_once_per_queryable_cluster(warehouse, gazetteer):
    executed = []

    class Cursor:
        def __init__(self):
            self.inner = warehouse.cursor()

        def execute(self, sql, params=None):
            executed.append((sql, params))
            self.inner.execute(sql, params)

        def fetchall(self):
            return self.inner.fetchall()

    run = _run()
    assets, segments, mentions = read_assets(run, gazetteer)
    result = resolve(run, assets, segments, mentions, gazetteer, CONFIG, DEFAULT_SETTINGS, Cursor)
    assert len(executed) == result.counts["cases_queried"] == 4
    assert all(sql.startswith("SELECT ") and sql.endswith(" LIMIT 6") for sql, _ in executed)


# --- contextual references ----------------------------------------------------------------------


def test_contextual_references_link_only_to_the_one_resolved_instance(world):
    assert _links(world, "a-email", "the product") == [("SameAs", "contextual", 1.0, ITEM[11])]
    assert _links(world, "a-chat", "The item") == [("SameAs", "contextual", 1.0, ITEM[11])]
    # The chat about RMA-6326426 names no item itself; its cluster resolved exactly one.
    assert _links(world, "b-chat", "the item") == [("SameAs", "contextual", 1.0, ITEM[10])]
    # Two possible items and none resolved: no guess.
    assert _links(world, "d-chat", "the item") == []


# --- entities and relationships ------------------------------------------------------------------


def test_a_brand_is_identified_by_the_item_row_it_lives_on(world):
    # Brand is mapped onto the item table: the case's item row names its brand, so the
    # brand cell is promoted and the entity carries the brand's name as an external ID.
    assert _links(world, "a-pdf", "brandcorp #1") == [("SameAs", "joint", 1.0, BRAND[1])]
    assert _links(world, "b-pdf", "scholarnameless #8") == [("SameAs", "joint", 1.0, BRAND[8])]
    brand = world.entities[ids.entity_id("Brand", BRAND[1])]
    assert brand.external_ids == [BRAND[1], "tpcds.item:i_brand=brandcorp #1"]
    assert brand.canonical_name == "brandcorp #1"


def test_the_return_entity_carries_the_warehouse_and_document_ids(world):
    entity = world.entities[ids.entity_id("Return", RETURN_A)]
    assert entity.external_ids == [
        RETURN_A,
        "documents.return:rma=RMA-3056773",
        "documents.return:case=CS-111222",
    ]
    assert entity.canonical_name == "RMA-3056773 (CS-111222)"
    unresolved = world.entities[ids.entity_id("Return", "documents.return:rma=RMA-7000001")]
    assert unresolved.external_ids == ["documents.return:rma=RMA-7000001"]
    customer = world.entities[ids.entity_id("Customer", CUSTOMER[2])]
    assert customer.canonical_name == "Wilma Graham (AAAAAAAAGLMDAAAB)"
    assert set(customer.external_ids) == {
        CUSTOMER[2],
        "tpcds.customer:c_customer_id=AAAAAAAAGLMDAAAB",
        "tpcds.customer:c_email_address=Wilma.Graham@t.edu",
    }
    assert len({e.entity_id for e in world.result.entities}) == len(world.result.entities)


def test_relationships_are_derived_from_links_and_the_identified_row(world):
    by_type = _group(world.result.relationships, lambda r: r.relationship_type)
    entity_key = {e.entity_id: e.external_ids[0] for e in world.result.entities}

    def triples(rtype):
        return {
            (
                r.source_id if r.source_kind == "asset" else entity_key[r.source_id],
                entity_key[r.target_id],
            )
            for r in by_type.get(rtype, [])
        }

    about = {r.source_id: entity_key[r.target_id] for r in by_type["About"]}
    assert len(by_type["About"]) == len(world.assets)  # exactly one per asset
    assert about["a-email"] == about["a-chat"] == about["a-pdf"] == RETURN_A
    assert about["b-pdf"] == about["b-chat"] == RETURN_B
    assert about["c-email"] == "documents.return:rma=RMA-7000001"
    assert about["d-chat"] == CUSTOMER[1]  # no Return known: the most mentioned entity
    assert {("a-email", CUSTOMER[2]), ("a-email", ITEM[11]), ("a-chat", STORE[9])} <= triples(
        "Mentions"
    )
    assert ("a-chat", CUSTOMER[1]) not in triples(
        "Mentions"
    )  # PossiblySameAs is not a mention edge
    assert (RETURN_A, SALE_A) in triples("ReturnOf")
    assert {(RETURN_A, ITEM[11]), (SALE_A, ITEM[11])} <= triples("Contains")
    # The sale's store and customer are the case's, not the sale row's own foreign keys.
    assert {(RETURN_A, STORE[9]), (SALE_A, STORE[9])} <= triples("LocatedAt")
    assert (SALE_A, STORE[8]) not in triples("LocatedAt")
    assert {(RETURN_A, CUSTOMER[2]), (SALE_A, CUSTOMER[2])} <= triples("PartyTo")
    assert (RETURN_A, REASON[1]) in triples("HasReason")
    structural = [r for r in world.result.relationships if r.resolved_by == "structured"]
    assert all(r.confidence == 1.0 and r.evidence_segment_ids for r in structural)
    assert all(r.resolved_by in ("joint", "exact_key", "heuristic") for r in by_type["About"])


def test_ids_are_stable_across_runs(warehouse, gazetteer, world):
    run = _run("run-2")
    assets, segments, mentions = read_assets(run, gazetteer)
    again = resolve(
        run, assets, segments, mentions, gazetteer, CONFIG, DEFAULT_SETTINGS, warehouse.cursor
    )
    assert [l.link_id for l in again.links] == [l.link_id for l in world.result.links]
    assert [e.entity_id for e in again.entities] == [e.entity_id for e in world.result.entities]
    assert [r.relationship_id for r in again.relationships] == [
        r.relationship_id for r in world.result.relationships
    ]
    assert all(l.crawl_run_id == "run-2" for l in again.links)


# --- in the crawl -------------------------------------------------------------------------------


def test_crawl_writes_entities_links_and_relationships(warehouse, gazetteer, tmp_path):
    from apps.helios.crawler.connectors import HeliosDsConnector, object_reader
    from apps.helios.crawler.crawl import CRAWLER_VERSION, crawl
    from helios_core.index.store import duckdb_index_store

    assert CRAWLER_VERSION == "0.7.0"
    root = tmp_path / "objects"
    con = duckdb.connect()
    con.execute("CREATE SCHEMA helios_ds")
    con.execute(
        "CREATE TABLE helios_ds.crawlable_artifacts (artifact_id VARCHAR, dataset_id VARCHAR, "
        "artifact_type VARCHAR, mime_type VARCHAR, source_locator VARCHAR, sha256 VARCHAR, "
        "size_bytes BIGINT, semantic_timestamp VARCHAR)"
    )
    kinds = {"message/rfc822": "email", "application/json": "chat", "application/pdf": "pdf"}
    for artifact_id, (mime, data) in ASSETS.items():
        key = f"datasets/ds-1/artifacts/{artifact_id}"
        (root / key).parent.mkdir(parents=True, exist_ok=True)
        (root / key).write_bytes(data)
        locator = {"connector_type": "helios_ds_file", "root": str(root), "key": key}
        con.execute(
            "INSERT INTO helios_ds.crawlable_artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                artifact_id,
                "ds-1",
                kinds[mime],
                mime,
                json.dumps(locator),
                hashlib.sha256(data).hexdigest(),
                len(data),
                None,
            ],
        )
    connector = HeliosDsConnector("ds-1", {"dataset_id": "ds-1"}, con.cursor, object_reader())
    index = duckdb_index_store()
    index.ensure_tables()

    def run_crawl():
        return crawl(
            index,
            connector,
            "ds-1",
            actor="test",
            ontology_version="0.2.0",
            settings=None,
            settings_hash=DEFAULT_SETTINGS.content_hash(),
            crawler_settings=DEFAULT_SETTINGS,
            gazetteer=gazetteer,
            resolution=CONFIG,
            warehouse_cursor=warehouse.cursor,
        )

    first = run_crawl()
    assert first.status == "SUCCEEDED" and first.counts["analyzed"] == 7
    assert first.counts["cases"] == 4 and first.counts["cases_resolved"] == 2
    links = index.read("helios_index.entity_links", {"crawl_run_id": first.crawl_run_id})
    entities = index.read("helios_index.entities", {"crawl_run_id": first.crawl_run_id})
    relationships = index.read("helios_index.relationships", {"crawl_run_id": first.crawl_run_id})
    assert first.counts["links_sameas"] == sum(1 for l in links if l.link_type == "SameAs") > 20
    assert (
        first.counts["links_possible"]
        == sum(1 for l in links if l.link_type == "PossiblySameAs")
        > 0
    )
    assert first.counts["entities"] == len(entities) and first.counts["relationships"] == len(
        relationships
    )
    assert {r.relationship_type for r in relationships} >= {
        "Mentions",
        "About",
        "ReturnOf",
        "Contains",
        "LocatedAt",
        "PartyTo",
        "HasReason",
    }

    second = run_crawl()  # unchanged assets are carried forward; resolution is recomputed
    assert second.counts["carried_forward"] == 7
    again = index.read("helios_index.entity_links", {"crawl_run_id": second.crawl_run_id})
    assert sorted(l.link_id for l in again) == sorted(l.link_id for l in links)
    assert all(l.crawl_run_id == second.crawl_run_id for l in again)
