"""CR-3: type detection and segmentation, with ground-truth-compatible locators."""

import pytest
from apps.helios.crawler.analyzers import analyze_asset, detect
from crawler_samples import RETAIL_SETTINGS, REPORT_LINES, chat_bytes, email_bytes, pdf_bytes
from helios_core.crawler.settings import CrawlerSettings


def test_detect_uses_the_bytes_not_the_declared_type():
    assert detect(pdf_bytes()) == "pdf"
    assert detect(email_bytes()) == "email"
    assert detect(chat_bytes()) == "chat"
    assert detect(b"\x89PNG\r\n") is None


def test_email_segments_header_subject_and_body_with_lf_line_endings():
    result = analyze_asset("message/rfc822", email_bytes(), RETAIL_SETTINGS)
    assert result.status == "analyzed"
    header, subject, body = result.segments
    assert header.locator == {"header": "From"}
    assert header.fields == {"display_name": "Wilma Graham", "address": "Wilma.Graham@t.edu"}
    assert subject.locator == {"part": "subject"}
    assert subject.text == "Problem with my ableeseantiantiought (ticket 205079)"
    assert body.locator["part"] == "body" and "\r" not in body.text
    assert "Box arrived torn; the product scratched." in body.text


def test_chat_has_one_segment_per_message_with_sender_and_role():
    result = analyze_asset("application/json", chat_bytes(), RETAIL_SETTINGS)
    assert [s.locator for s in result.segments] == [
        {"message_id": "msg-1"},
        {"message_id": "msg-2"},
    ]
    assert result.segments[1].fields["role"] == "inspector"
    assert result.segments[1].text == "Box was crushed, the item has dents."
    unknown = analyze_asset("application/json", chat_bytes("other/1"), RETAIL_SETTINGS)
    assert unknown.status == "invalid" and "fits none of the chat layouts" in unknown.detail


def test_pdf_pages_with_label_values_and_a_table():
    result = analyze_asset("application/pdf", pdf_bytes(), RETAIL_SETTINGS)
    assert result.status == "analyzed"
    [page] = result.segments
    assert page.locator == {"page": 1}
    assert "Outer packaging crushed on two corners." in page.text
    pairs = {p["label"]: p["value"] for p in page.fields["labels"]}
    assert pairs["RMA number"] == "RMA-6326426"
    assert pairs["Store"] == "ese (#AAAAAAAAEAAAAAAA), Midway, TN"
    assert pairs["Original receipt ticket"] == "166147"
    assert pairs["Customer ID"] == "AAAAAAAADPDEAAAA"
    assert pairs["Name"] == "Angela Raymond"
    [table] = page.fields["tables"]
    assert table["columns"] == ["Item ID", "Description", "Brand", "Category", "Qty"]
    assert table["rows"] == [
        ["AAAAAAAAFCOBAAAA", "ationoughtationation", "scholarnameless #8", "Home / tables", "64"]
    ]
    assert "Item ID" not in pairs  # table headers are not read as label/value pairs
    assert REPORT_LINES  # (fixture lines are what the page shows)


@pytest.mark.parametrize(
    "mime, data, status",
    [
        ("application/pdf", b"From: x@y.z\n\nhello", "type_mismatch"),
        ("image/png", b"\x89PNG\r\n", "unsupported"),
    ],
)
def test_wrong_or_unsupported_types_are_recorded_not_guessed(mime, data, status):
    assert analyze_asset(mime, data, RETAIL_SETTINGS).status == status


def test_disabled_analyzers_and_page_limits_follow_the_settings():
    doc = RETAIL_SETTINGS.model_dump(mode="json")
    doc["analyzers"]["chat"]["enabled"] = False
    doc["analyzers"]["pdf"]["max_pages"] = 1
    settings = CrawlerSettings.model_validate(doc)
    assert analyze_asset("application/json", chat_bytes(), settings).status == "unsupported"
    two_pages = pdf_bytes(REPORT_LINES + ["x"] * 60)
    result = analyze_asset("application/pdf", two_pages, settings)
    assert len(result.segments) == 1 and result.detail.startswith("read 1 of ")


# --- CG-7: layouts are settings, not code ------------------------------------------------


def _with_analyzers(**changes):
    document = RETAIL_SETTINGS.model_dump(mode="json", by_alias=True)
    for kind, options in changes.items():
        document["analyzers"][kind].update(options)
    return CrawlerSettings.model_validate(document)


SLACK_STYLE = {
    "export": {"kind": "support-desk", "version": 3},
    "channel": {"id": "C042"},
    "members": [{"id": "U1", "kind": "customer"}, {"id": "U2", "kind": "agent"}],
    "data": {
        "events": [
            {"ts": "1718000000.1", "user": {"id": "U1", "label": "Dana K."}, "body": "My order arrived broken.", "at": "2024-06-10T08:00:00Z"},
            {"ts": "1718000060.2", "user": {"id": "U2", "label": "Sam"}, "body": "Sorry to hear that.", "at": "2024-06-10T08:01:00Z"},
        ]
    },
}
SLACK_LAYOUT = {
    "name": "support desk export",
    "match": {"export.kind": "support-desk"},
    "messages": "data.events",
    "message_id": "ts",
    "text": "body",
    "sender": "user.id",
    "sender_name": "user.label",
    "timestamp": "at",
    "thread_id": "channel.id",
    "participants": "members",
    "participant_id": "id",
    "participant_role": "kind",
}


def test_a_second_chat_layout_is_read_by_configuration_alone():
    import json

    data = json.dumps(SLACK_STYLE).encode()
    # With only the shipped layout the file is not even recognised as a chat.
    unread = analyze_asset("application/json", data, RETAIL_SETTINGS)
    assert unread.status == "type_mismatch"

    layouts = RETAIL_SETTINGS.model_dump(mode="json")["analyzers"]["chat"]["layouts"]
    both = _with_analyzers(chat={"layouts": [*layouts, SLACK_LAYOUT]})
    result = analyze_asset("application/json", data, both)
    assert result.status == "analyzed"
    assert [(s.segment_type, s.locator, s.text) for s in result.segments] == [
        ("message", {"message_id": "1718000000.1"}, "My order arrived broken."),
        ("message", {"message_id": "1718000060.2"}, "Sorry to hear that."),
    ]
    assert result.segments[0].fields == {
        "sender": "U1",
        "sender_name": "Dana K.",
        "role": "customer",
        "timestamp": "2024-06-10T08:00:00Z",
        "thread_id": "C042",
    }
    assert result.segments[1].fields["role"] == "agent"
    # The original format is still read, by its own layout.
    assert analyze_asset("application/json", chat_bytes(), both).status == "analyzed"


def test_a_layout_can_take_the_role_from_each_message_and_must_match_its_file():
    import json

    thread = {"kind": "tickets", "items": [{"id": "m1", "msg": "Hello", "who": "ann", "as": "agent"}]}
    layout = {"name": "tickets", "match": {"kind": "tickets"}, "messages": "items", "message_id": "id",
              "text": "msg", "sender": "who", "sender_name": None, "timestamp": None, "thread_id": None,
              "participants": None, "role": "as"}
    settings = _with_analyzers(chat={"layouts": [layout]})
    result = analyze_asset("application/json", json.dumps(thread).encode(), settings)
    assert result.segments[0].fields == {"sender": "ann", "sender_name": None, "role": "agent", "timestamp": None, "thread_id": None}

    other = json.dumps({**thread, "kind": "something-else"}).encode()
    refused = analyze_asset("application/json", other, settings)
    assert refused.status == "invalid" and "fits none of the chat layouts" in refused.detail
    broken = json.dumps({"kind": "tickets", "items": [{"id": "m1"}]}).encode()
    assert analyze_asset("application/json", broken, settings).detail == "a chat message lacks its ID or its text"
    assert analyze_asset("application/json", chat_bytes(), _with_analyzers(chat={"layouts": []})).status == "type_mismatch"


def test_settings_saved_with_format_names_still_read_those_formats():
    document = RETAIL_SETTINGS.model_dump(mode="json", by_alias=True)
    document["analyzers"]["chat"] = {"enabled": True, "schemas": ["helios-ds/chat-thread/1.0", "other/1"]}
    older = CrawlerSettings.model_validate(document)
    assert [l.name for l in older.analyzers.chat.layouts] == ["helios-ds/chat-thread/1.0", "other/1"]
    assert older.analyzers.chat.layouts[0] == RETAIL_SETTINGS.analyzers.chat.layouts[0]
    assert analyze_asset("application/json", chat_bytes("other/1"), older).status == "analyzed"


def test_what_counts_as_a_table_heading_is_a_setting():
    from apps.helios.crawler.analyzers import _pdf_fields
    from helios_core.crawler.settings import PdfAnalyzer

    labels = {rule.label.lower(): rule for rule in RETAIL_SETTINGS.pdf_labels}
    page = "\n".join(["Item ID", "Description", "Brand", "Units", "AAAAAAAAFCOBAAAA", "side tables", "scholarnameless", "64"])
    shipped = _pdf_fields(page, labels, RETAIL_SETTINGS.analyzers.pdf)
    assert shipped["tables"][0]["columns"] == ["Item ID", "Description", "Brand", "Units"]
    assert shipped["tables"][0]["rows"] == [["AAAAAAAAFCOBAAAA", "side tables", "scholarnameless", "64"]]
    # The engine alone does not know that a 16-letter code is a value, so it reads it as a
    # fifth heading and finds no table of that shape.
    plain = _pdf_fields(page, labels, PdfAnalyzer())
    assert "tables" not in plain or plain["tables"][0]["columns"] != shipped["tables"][0]["columns"]
    assert _pdf_fields(page, labels, PdfAnalyzer(value_patterns=[r"[\d$@]", r"^[A-P]{16}$"])) == shipped
    wide_only = RETAIL_SETTINGS.analyzers.pdf.model_copy(update={"table_min_columns": 5})
    assert "tables" not in _pdf_fields(page, labels, wide_only)
    with pytest.raises(ValueError, match="not a valid regular expression"):
        PdfAnalyzer(value_patterns=["("])
