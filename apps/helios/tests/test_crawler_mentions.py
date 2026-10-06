"""CR-4: mention extraction (identifier patterns, the TPC-DS gazetteer, PDF
structure and contextual phrases) on real-shaped email, chat and PDF assets."""

import json
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path

import pytest
from apps.helios.crawler.analyzers import analyze_asset
from apps.helios.crawler.connectors import SourceAsset
from apps.helios.crawler.crawl import MENTIONS, segment_rows
from apps.helios.crawler.gazetteer import Gazetteer, has_names, is_low_specificity, tokens
from apps.helios.crawler.mentions import extract_mentions
from crawler_samples import RETAIL_SETTINGS, chat_bytes, pdf_bytes
from helios_core.index.records import CrawlRunRecord
from helios_core.ontology.mapping import load_mapping, resolution_config

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG = resolution_config(load_mapping(REPO_ROOT / "ontology/mappings/ossie/tpcds.yaml"))

# A handful of warehouse rows, as the mapping's identifier columns would fetch them.
ROWS = {
    "Customer": [
        {
            "c_customer_sk": 1,
            "c_customer_id": "AAAAAAAADPDEAAAA",
            "c_email_address": "Angela.Raymond@7T.edu",
            "c_first_name": "Angela",
            "c_last_name": "Raymond",
            "c_salutation": "Mrs.",
        },
        {
            "c_customer_sk": 2,
            "c_customer_id": "AAAAAAAAGLMDAAAB",
            "c_email_address": "Wilma.Graham@t.edu",
            "c_first_name": "Wilma",
            "c_last_name": "Graham",
            "c_salutation": "Dr.",
        },
        {  # no first name: the display form is a bare surname
            "c_customer_sk": 3,
            "c_customer_id": "AAAAAAAAGLMDAAAC",
            "c_email_address": None,
            "c_first_name": None,
            "c_last_name": "Smith",
            "c_salutation": "Mr.",
        },
        {  # the same row again (a SELECT DISTINCT never returns it, but rows may)
            "c_customer_sk": 3,
            "c_customer_id": "AAAAAAAAGLMDAAAC",
            "c_email_address": None,
            "c_first_name": None,
            "c_last_name": "Smith",
            "c_salutation": "Mr.",
        },
    ],
    "Store": [
        {
            "s_store_sk": 4,
            "s_store_id": "AAAAAAAAEAAAAAAA",
            "s_store_name": "ese",
            "s_city": "Midway",
        },
        {
            "s_store_sk": 8,
            "s_store_id": "AAAAAAAAIAAAAAAA",
            "s_store_name": "eing",
            "s_city": "Fairview",
        },
        {
            "s_store_sk": 9,
            "s_store_id": "AAAAAAAAIAAAAAAA",
            "s_store_name": "eing",
            "s_city": "Midway",
        },
    ],
    "Item": [
        {
            "i_item_sk": 10,
            "i_item_id": "AAAAAAAAFCOBAAAA",
            "i_product_name": "ationoughtationation",
            "i_color": "medium",
            "i_class": "tables",
        },
        {
            "i_item_sk": 11,
            "i_item_id": "AAAAAAAAGLMDAAAA",
            "i_product_name": "ableeseantiantiought",
            "i_color": "blanched",
            "i_class": "fragrances",
        },
    ],
    "Brand": [
        {"i_brand_id": 8, "i_brand": "scholarnameless #8"},
        {"i_brand_id": 8, "i_brand": "scholarnameless #8"},  # one row per item, same brand
        {"i_brand_id": 8, "i_brand": "scholar nameless #8"},  # a brand ID with two spellings
    ],
    "Reason": [
        {
            "r_reason_sk": 1,
            "r_reason_id": "AAAAAAAABAAAAAAA",
            "r_reason_desc": "Package was damaged",
        }
    ],
}


@pytest.fixture(scope="module")
def gazetteer():
    return Gazetteer.from_rows(ROWS, CONFIG, RETAIL_SETTINGS.dictionary)


def _run(run_id="run-1"):
    return CrawlRunRecord(
        crawl_run_id=run_id,
        connector="helios_ds",
        source="ds-1",
        ontology_version="0.2.0",
        crawler_version="0.4.0",
        status="RUNNING",
        started_at="2026-01-01T00:00:00Z",
        recorded_at="2026-01-01T00:00:00Z",
        actor="test",
    )


def _mentions(gazetteer, mime, data, run=None):
    run = run or _run()
    asset = SourceAsset(asset_id="asset-1", source="ds-1", mime_type=mime, locator={}, version="v1")
    analysis = analyze_asset(mime, data, RETAIL_SETTINGS)
    assert analysis.status == "analyzed", analysis.detail
    segments = segment_rows(run, asset, analysis.segments)
    mentions = extract_mentions(run, asset, segments, RETAIL_SETTINGS, gazetteer, CONFIG)
    return segments, mentions


def _by_locator(segments, mentions, **locator):
    """(class, extractor, surface) of the mentions in the segment with ``locator``."""
    [segment] = [s for s in segments if all(s.locator.get(k) == v for k, v in locator.items())]
    found = [m for m in mentions if m.segment_id == segment.segment_id]
    for m in found:  # offsets always address the surface form in the segment text
        if m.start_offset is not None:
            assert segment.text[m.start_offset : m.end_offset] == m.surface_form
    return {(m.proposed_class, m.extractor, m.surface_form) for m in found}


def sample_email_bytes() -> bytes:
    message = EmailMessage(policy=SMTP)
    message["From"] = "Wilma Graham <Wilma.Graham@t.edu>"
    message["To"] = "care@helios-retail.example"
    message["Subject"] = "Problem with my ableeseantiantiought (ticket 205079)"
    message["Date"] = "Tue, 04 May 1999 09:08:31 +0000"
    message.set_content(
        "Re: blanched fragrances item (AAAAAAAAGLMDAAAA), receipt ending in 5079, returned "
        "May 4, 1999. Box arrived torn; the product scratched. The return slip says: "
        "Package was damaged.\n\nReturn # RMA-3056773. When will the refund post?\n\n"
        "Thanks.\nWilma Graham\nWilma.Graham@t.edu\n",
        charset="utf-8",
        cte="quoted-printable",
    )
    return message.as_bytes()


def sample_chat_bytes(*texts: str) -> bytes:
    thread = json.loads(chat_bytes())
    thread["messages"] = [
        {**thread["messages"][0], "message_id": f"msg-{i}", "text": text}
        for i, text in enumerate(texts, start=1)
    ]
    return json.dumps(thread).encode()


# --- the gazetteer ------------------------------------------------------------------------


def test_gazetteer_builds_forms_from_the_mapping_columns(gazetteer):
    forms = {(f.class_name, f.surface): f for f in gazetteer.forms.values()}
    assert forms["Customer", "angela.raymond@7t.edu"].kind == "key"
    assert forms["Customer", "angela.raymond@7t.edu"].columns == "c_email_address"
    assert forms["Customer", "angela raymond"].kind == "display"
    assert forms["Customer", "angela raymond"].columns == "c_first_name,c_last_name"
    assert forms["Customer", "mrs. raymond"].kind == "template"
    assert forms["Customer", "mrs. raymond"].columns == "c_salutation,c_last_name"
    assert forms["Store", "midway store"].kind == "template"
    assert forms["Item", "blanched fragrances item"].kind == "template"
    assert forms["Item", "blanched fragrances"].kind == "template"
    assert forms["Brand", "scholarnameless #8"].kind == "display"  # display beats alias
    assert forms["Reason", "package was damaged"].kind == "display"
    assert forms["Customer", "smith"].instances == ("tpcds.customer:c_customer_sk=3",)
    assert forms["Brand", "scholar nameless #8"].instances == ("tpcds.item:i_brand_id=8",)
    assert forms["Store", "eing"].instances == (
        "tpcds.store:s_store_sk=8",
        "tpcds.store:s_store_sk=9",
    )
    assert forms["Store", "eing"].specificity == 0.5
    assert forms["Brand", "scholarnameless #8"].instances == ("tpcds.item:i_brand_id=8",)


def test_low_specificity_forms(gazetteer):
    forms = {(f.class_name, f.surface): f for f in gazetteer.forms.values()}
    assert forms["Store", "ese"].low_specificity  # short
    assert forms["Store", "eing"].low_specificity
    assert forms["Customer", "smith"].low_specificity  # a partial name
    assert not forms["Customer", "mr. smith"].low_specificity
    assert not forms["Customer", "angela raymond"].low_specificity
    assert not forms["Item", "ationoughtationation"].low_specificity
    rules = RETAIL_SETTINGS.dictionary
    assert is_low_specificity("ought", 1, rules=rules)  # a TPC-DS number word, listed as ordinary
    assert is_low_specificity("able", 1, rules=rules)
    assert is_low_specificity("dr. smith", 444, rules=rules)  # shared by too many
    assert not is_low_specificity("dr. smith", 44, rules=rules)
    assert not is_low_specificity("ought", 1)  # the engine itself calls no word ordinary
    assert is_low_specificity("able", 1)  # ... but a short single word is never specific


def test_key_lookup_and_class_of_key(gazetteer):
    assert gazetteer.lookup("c_customer_id", "aaaaaaaadpdeaaaa").class_name == "Customer"
    assert gazetteer.lookup("i_item_id", "AAAAAAAAFCOBAAAA").instances == (
        "tpcds.item:i_item_sk=10",
    )
    assert gazetteer.lookup("s_store_id", "AAAAAAAAFCOBAAAA") is None
    columns = ["i_item_id", "c_customer_id", "s_store_id"]
    assert gazetteer.class_of_key(columns, "AAAAAAAAEAAAAAAA") == "Store"
    assert gazetteer.class_of_key(columns, "AAAAAAAAPPPPAAAA") is None
    assert set(gazetteer.key_forms_by_column) == {
        "c_customer_id",
        "c_email_address",
        "i_item_id",
        "s_store_id",
        "r_reason_id",
    }
    assert gazetteer.column_classes["s_city"] == "Store"


def test_scan_is_case_insensitive_longest_first_on_word_boundaries(gazetteer):
    text = "MRS. RAYMOND returned the Blanched Fragrances Item; eseese isn't a store name."
    hits = gazetteer.scan(text)
    assert [(h.surface, h.class_name, h.kind) for h in hits] == [
        ("MRS. RAYMOND", "Customer", "template"),
        ("Blanched Fragrances Item", "Item", "template"),
    ]
    assert text[hits[0].start : hits[0].end] == "MRS. RAYMOND"
    assert hits[0].columns == "c_salutation,c_last_name"
    assert [t for t, _, _ in tokens("Wilma.Graham@t.edu. RMA-1 (#8)")] == [
        "wilma.graham@t.edu",
        ".",
        "rma-1",
        "(",
        "#",
        "8",
        ")",
    ]


def test_gazetteer_round_trips_through_a_dict(gazetteer):
    copy = Gazetteer.from_dict(gazetteer.to_dict(), CONFIG)
    assert copy.classes == gazetteer.classes
    assert copy.forms == gazetteer.forms
    assert [h.surface for h in copy.scan("Angela Raymond")] == ["Angela Raymond"]


def test_build_reads_each_class_table_and_skips_classes_without_names():
    executed = []

    class Cursor:
        def execute(self, sql, params=None):
            executed.append(sql)
            self.table = sql.rsplit(" ", 1)[1]

        def fetchall(self):
            return {
                "tpcds.store": [(4, "AAAAAAAAEAAAAAAA", "ese", "Midway")],
                "tpcds.reason": [(1, "AAAAAAAABAAAAAAA", "Package was damaged")],
            }[self.table]

    built = Gazetteer.build(Cursor, CONFIG, ["Store", "Reason", "Sale", "Return"], RETAIL_SETTINGS.dictionary)
    assert executed == [
        "SELECT DISTINCT s_store_sk, s_store_id, s_store_name, s_city FROM tpcds.store",
        "SELECT DISTINCT r_reason_sk, r_reason_id, r_reason_desc FROM tpcds.reason",
    ]
    assert built.classes == ["Reason", "Store"]  # Sale and Return have only ticket numbers
    assert not has_names(CONFIG.classes["Sale"])
    assert [h.class_name for h in built.scan("Package was damaged at the Midway store")] == [
        "Reason",
        "Store",
    ]


def test_build_refuses_unsafe_identifiers():
    bad = CONFIG.model_copy(
        update={
            "classes": {
                "Store": CONFIG.classes["Store"].model_copy(
                    update={"ossie_element": "store; DROP TABLE x"}
                )
            }
        }
    )
    with pytest.raises(ValueError, match="identifier"):
        Gazetteer.build(lambda: None, bad, ["Store"])


# --- the extractors -------------------------------------------------------------------------


def test_email_mentions(gazetteer):
    segments, mentions = _mentions(gazetteer, "message/rfc822", sample_email_bytes())
    header = _by_locator(segments, mentions, header="From")
    assert header == {
        ("Customer", "pattern", "Wilma Graham"),
        ("Customer", "pattern", "Wilma.Graham@t.edu"),
    }
    [name] = [m for m in mentions if m.extractor_detail == "header:From"]
    assert name.locator == {"header": "From", "start": 0, "end": 12}

    assert _by_locator(segments, mentions, part="subject") == {
        ("Item", "gazetteer", "ableeseantiantiought"),
        ("Sale", "pattern", "205079"),
    }

    body = _by_locator(segments, mentions, part="body")
    assert body == {
        ("Item", "gazetteer", "blanched fragrances item"),
        ("Item", "pattern", "AAAAAAAAGLMDAAAA"),
        ("Sale", "pattern", "5079"),
        (None, "pattern", "May 4, 1999"),
        ("Item", "contextual", "the product"),
        ("Return", "contextual", "The return"),
        ("Reason", "gazetteer", "Package was damaged"),
        ("Return", "pattern", "RMA-3056773"),
        ("Customer", "gazetteer", "Wilma Graham"),
        ("Customer", "pattern", "Wilma.Graham@t.edu"),
    }
    details = {m.surface_form: m.extractor_detail for m in mentions}
    assert details["5079"] == "receipt_tail"
    assert details["AAAAAAAAGLMDAAAA"] == "tpcds_business_id"
    assert details["blanched fragrances item"] == "i_color,i_class"
    assert details["the product"] == "the product"
    [tail] = [m for m in mentions if m.surface_form == "5079"]
    assert tail.locator == {"part": "body", "start": 67, "end": 71}
    assert all(m.ontology_version == "0.2.0" and m.asset_id == "asset-1" for m in mentions)


def test_chat_mentions(gazetteer):
    segments, mentions = _mentions(
        gazetteer,
        "application/json",
        sample_chat_bytes(
            "Got an email from Angela.Raymond@7T.edu about return RMA-6326426, "
            "the ationoughtationation from the Midway store.",
            "Angela Raymond wants the $310.40 refund for this return. Case CS-954939.",
        ),
    )
    assert _by_locator(segments, mentions, message_id="msg-1") == {
        ("Customer", "pattern", "Angela.Raymond@7T.edu"),
        ("Return", "pattern", "RMA-6326426"),
        ("Item", "gazetteer", "ationoughtationation"),
        ("Store", "gazetteer", "Midway store"),
    }
    assert _by_locator(segments, mentions, message_id="msg-2") == {
        ("Customer", "gazetteer", "Angela Raymond"),
        (None, "pattern", "$310.40"),
        ("Return", "contextual", "this return"),
        ("Return", "pattern", "CS-954939"),
    }
    [store] = [m for m in mentions if m.surface_form == "Midway store"]
    assert store.extractor_detail == "s_city"
    assert store.locator == {"message_id": "msg-1", "start": 100, "end": 112}


def test_low_specificity_names_need_a_cue(gazetteer):
    segments, mentions = _mentions(
        gazetteer,
        "application/json",
        sample_chat_bytes(
            "ese is a word I like.",
            "Smith is a common surname.",
            "She bought it at the eing store in Fairview.",
            "Mr. Smith called about it.",
        ),
    )
    assert _by_locator(segments, mentions, message_id="msg-1") == set()
    # The bare surname is a partial name: it counts only because msg-4 anchors Mr. Smith.
    assert _by_locator(segments, mentions, message_id="msg-2") == {
        ("Customer", "gazetteer", "Smith")
    }
    assert _by_locator(segments, mentions, message_id="msg-3") == {
        ("Store", "gazetteer", "eing"),
    }
    assert _by_locator(segments, mentions, message_id="msg-4") == {
        ("Customer", "gazetteer", "Mr. Smith")
    }


def test_partial_names_count_only_next_to_an_anchored_instance(gazetteer):
    forms = {(f.class_name, f.surface): f for f in gazetteer.forms.values()}
    assert forms["Customer", "wilma"].partial and forms["Customer", "wilma"].low_specificity
    assert forms["Customer", "graham"].instances == ("tpcds.customer:c_customer_sk=2",)
    assert not forms["Customer", "wilma graham"].partial

    # Anchored by the From header: the sign-off's bare first name counts.
    segments, mentions = _mentions(gazetteer, "message/rfc822", sample_email_bytes())
    body = _by_locator(segments, mentions, part="body")
    assert ("Customer", "gazetteer", "Wilma Graham") in body
    assert ("Customer", "gazetteer", "Wilma") not in body  # inside the longer full name

    def signed(sign_off: str, anchor: str = "") -> bytes:
        message = EmailMessage(policy=SMTP)
        message["From"] = anchor or "care@helios-retail.example"
        message["Subject"] = "Where is my refund?"
        message.set_content(f"Hello,\n\nStill waiting.\n\nThx,\n{sign_off}\n")
        return message.as_bytes()

    segments, mentions = _mentions(gazetteer, "message/rfc822", signed("Wilma"))
    assert _by_locator(segments, mentions, part="body") == set()  # nothing anchors her
    segments, mentions = _mentions(
        gazetteer, "message/rfc822", signed("Wilma", "Wilma Graham <Wilma.Graham@t.edu>")
    )
    assert _by_locator(segments, mentions, part="body") == {("Customer", "gazetteer", "Wilma")}
    # A key alone anchors too, and the anchored customer's surname counts as well.
    segments, mentions = _mentions(
        gazetteer, "message/rfc822", signed("Graham", "<Wilma.Graham@t.edu>")
    )
    assert _by_locator(segments, mentions, part="body") == {("Customer", "gazetteer", "Graham")}
    # A different customer's key does not anchor her.
    segments, mentions = _mentions(
        gazetteer, "message/rfc822", signed("Wilma", "<Angela.Raymond@7T.edu>")
    )
    assert _by_locator(segments, mentions, part="body") == set()


def test_a_name_confirmed_elsewhere_in_the_asset_counts_without_a_cue(gazetteer):
    segments, mentions = _mentions(
        gazetteer,
        "application/json",
        sample_chat_bytes(
            "ese is a word I like.",
            "It came back to ese (#AAAAAAAAEAAAAAAA) yesterday.",
        ),
    )
    assert _by_locator(segments, mentions, message_id="msg-1") == {("Store", "gazetteer", "ese")}
    assert _by_locator(segments, mentions, message_id="msg-2") == {
        ("Store", "gazetteer", "ese"),
        ("Store", "pattern", "AAAAAAAAEAAAAAAA"),
    }


def test_one_class_per_span_chosen_by_the_nearest_cue(gazetteer):
    # Store 4 and item 4 share the business key; the context decides.
    with_item = {
        **ROWS,
        "Item": ROWS["Item"]
        + [{"i_item_sk": 4, "i_item_id": "AAAAAAAAEAAAAAAA", "i_product_name": "ese"}],
    }
    both = Gazetteer.from_rows(with_item, CONFIG, RETAIL_SETTINGS.dictionary)
    assert both.lookup("i_item_id", "AAAAAAAAEAAAAAAA") and both.lookup(
        "s_store_id", "AAAAAAAAEAAAAAAA"
    )
    segments, mentions = _mentions(
        both,
        "application/json",
        sample_chat_bytes(
            "Return received at store AAAAAAAAEAAAAAAA yesterday.",
            "Heads up: AAAAAAAAEAAAAAAA came back.",
            "The item below was returned at ese (Midway, TN) today.",
        ),
    )
    assert _by_locator(segments, mentions, message_id="msg-1") == {
        ("Store", "pattern", "AAAAAAAAEAAAAAAA"),
    }
    # No cue: the rule's column order (i_item_id first) decides.
    assert _by_locator(segments, mentions, message_id="msg-2") == {
        ("Item", "pattern", "AAAAAAAAEAAAAAAA"),
    }
    assert _by_locator(segments, mentions, message_id="msg-3") == {
        ("Store", "gazetteer", "ese"),
        ("Item", "contextual", "The item"),
    }


def test_pdf_label_and_table_mentions(gazetteer):
    segments, mentions = _mentions(gazetteer, "application/pdf", pdf_bytes())
    page = _by_locator(segments, mentions, page=1)
    assert page == {
        ("Customer", "gazetteer", "Mrs. Raymond"),
        ("Item", "gazetteer", "medium tables item"),
        ("Store", "gazetteer", "Midway store"),
        ("Return", "pattern", "RMA-6326426"),
        ("Store", "gazetteer", "ese"),
        ("Store", "pattern", "AAAAAAAAEAAAAAAA"),
        ("Sale", "pattern", "166147"),
        ("Item", "pattern", "AAAAAAAAFCOBAAAA"),
        ("Item", "gazetteer", "ationoughtationation"),
        ("Brand", "gazetteer", "scholarnameless #8"),
        ("Customer", "gazetteer", "Angela Raymond"),
        ("Customer", "pattern", "AAAAAAAADPDEAAAA"),
    }
    [segment] = segments
    for m in mentions:
        assert m.locator["page"] == 1 and m.locator["text"] == m.surface_form
        assert segment.text[m.locator["start"] : m.locator["end"]] == m.surface_form


def test_pdf_values_unknown_to_the_gazetteer_still_come_from_labels_and_cells(gazetteer):
    lines = [
        "RMA number",
        "RMA-0000001",
        "Store",
        "ought (#AAAAAAAABAAAAAAA), Midway, TN",
        "Name",
        "Nobody Known",
        "Email",
        "Nobody.Known@x.org",
        "Item ID",
        "Description",
        "Brand",
        "Category",
        "AAAAAAAAPPPPAAAA",
        "unknownproduct",
        "unknownbrand #1",
        "Home / tables",
    ]
    segments, mentions = _mentions(gazetteer, "application/pdf", pdf_bytes(lines))
    by_surface = {m.surface_form: m for m in mentions}
    assert by_surface["ought (#AAAAAAAABAAAAAAA), Midway, TN"].extractor_detail == "label:Store"
    assert by_surface["ought (#AAAAAAAABAAAAAAA), Midway, TN"].proposed_class == "Store"
    assert by_surface["Nobody Known"].extractor_detail == "label:Name"
    assert by_surface["Nobody.Known@x.org"].proposed_class == "Customer"
    assert by_surface["RMA-0000001"].proposed_class == "Return"
    cell = by_surface["AAAAAAAAPPPPAAAA"]
    assert (cell.proposed_class, cell.extractor_detail) == ("Item", "table:Item ID")
    assert by_surface["unknownproduct"].extractor_detail == "table:Description"
    assert by_surface["unknownbrand #1"].proposed_class == "Brand"
    assert "Home / tables" not in by_surface  # Category is not a labelled class
    [segment] = segments
    for m in mentions:
        assert segment.text[m.start_offset : m.end_offset] == m.surface_form


def test_mention_ids_are_stable_across_runs_and_rows_are_sorted(gazetteer):
    _, first = _mentions(gazetteer, "message/rfc822", sample_email_bytes(), _run("run-1"))
    _, second = _mentions(gazetteer, "message/rfc822", sample_email_bytes(), _run("run-2"))
    assert [m.mention_id for m in first] == [m.mention_id for m in second]
    assert len({m.mention_id for m in first}) == len(first)
    # Header, subject, body: segment order, then offset order within each.
    order = {m.segment_id: i for i, m in enumerate(first)}
    assert [m.segment_id for m in first] == sorted(order, key=order.get) or len(order) <= 3
    for segment_id in order:
        offsets = [m.start_offset for m in first if m.segment_id == segment_id]
        assert offsets == sorted(offsets)


# --- in the crawl ---------------------------------------------------------------------------


def test_crawl_writes_mentions_and_carries_them_forward(gazetteer, tmp_path):
    import hashlib

    import duckdb
    from apps.helios.crawler.connectors import HeliosDsConnector, object_reader
    from apps.helios.crawler.crawl import crawl
    from helios_core.index.store import duckdb_index_store

    root = tmp_path / "objects"
    con = duckdb.connect()
    con.execute("CREATE SCHEMA helios_ds")
    con.execute(
        "CREATE TABLE helios_ds.crawlable_artifacts (artifact_id VARCHAR, dataset_id VARCHAR, "
        "artifact_type VARCHAR, mime_type VARCHAR, source_locator VARCHAR, sha256 VARCHAR, "
        "size_bytes BIGINT, semantic_timestamp VARCHAR)"
    )
    files = {
        "a-pdf": ("pdf", "application/pdf", pdf_bytes()),
        "b-email": ("email", "message/rfc822", sample_email_bytes()),
    }
    for artifact_id, (kind, mime, data) in files.items():
        key = f"datasets/ds-1/artifacts/{artifact_id}"
        (root / key).parent.mkdir(parents=True, exist_ok=True)
        (root / key).write_bytes(data)
        locator = {"connector_type": "helios_ds_file", "root": str(root), "key": key}
        con.execute(
            "INSERT INTO helios_ds.crawlable_artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                artifact_id,
                "ds-1",
                kind,
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
    hook_calls = []

    def run_crawl(full=False):
        return crawl(
            index,
            connector,
            "ds-1",
            actor="test",
            ontology_version="0.2.0",
            settings=None,
            settings_hash=RETAIL_SETTINGS.content_hash(),
            crawler_settings=RETAIL_SETTINGS,
            analyze=lambda run, fetched, segments, mentions: hook_calls.append(
                (fetched.asset.asset_id, len(mentions))
            ),
            full=full,
            gazetteer=gazetteer,
            resolution=CONFIG,
        )

    first = run_crawl()
    assert first.status == "SUCCEEDED" and first.counts["analyzed"] == 2
    written = index.read(MENTIONS, {"crawl_run_id": first.crawl_run_id})
    assert first.counts["mentions"] == len(written) > 20
    assert dict(hook_calls) == {
        "a-pdf": sum(1 for m in written if m.asset_id == "a-pdf"),
        "b-email": sum(1 for m in written if m.asset_id == "b-email"),
    }
    assert {m.proposed_class for m in written} >= {"Customer", "Item", "Store", "Brand", "Sale"}

    second = run_crawl()
    assert second.counts["carried_forward"] == 2
    assert second.counts["mentions"] == first.counts["mentions"]
    carried = index.read(MENTIONS, {"crawl_run_id": second.crawl_run_id})
    assert sorted(m.mention_id for m in carried) == sorted(m.mention_id for m in written)
    assert all(m.crawl_run_id == second.crawl_run_id for m in carried)
