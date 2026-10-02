"""CR-3: type detection and segmentation, with ground-truth-compatible locators."""

import pytest
from apps.helios.crawler.analyzers import analyze_asset, detect
from crawler_samples import REPORT_LINES, chat_bytes, email_bytes, pdf_bytes
from helios_core.crawler.settings import DEFAULT_SETTINGS, CrawlerSettings


def test_detect_uses_the_bytes_not_the_declared_type():
    assert detect(pdf_bytes()) == "pdf"
    assert detect(email_bytes()) == "email"
    assert detect(chat_bytes()) == "chat"
    assert detect(b"\x89PNG\r\n") is None


def test_email_segments_header_subject_and_body_with_lf_line_endings():
    result = analyze_asset("message/rfc822", email_bytes(), DEFAULT_SETTINGS)
    assert result.status == "analyzed"
    header, subject, body = result.segments
    assert header.locator == {"header": "From"}
    assert header.fields == {"display_name": "Wilma Graham", "address": "Wilma.Graham@t.edu"}
    assert subject.locator == {"part": "subject"}
    assert subject.text == "Problem with my ableeseantiantiought (ticket 205079)"
    assert body.locator["part"] == "body" and "\r" not in body.text
    assert "Box arrived torn; the product scratched." in body.text


def test_chat_has_one_segment_per_message_with_sender_and_role():
    result = analyze_asset("application/json", chat_bytes(), DEFAULT_SETTINGS)
    assert [s.locator for s in result.segments] == [
        {"message_id": "msg-1"},
        {"message_id": "msg-2"},
    ]
    assert result.segments[1].fields["role"] == "inspector"
    assert result.segments[1].text == "Box was crushed, the item has dents."
    unknown = analyze_asset("application/json", chat_bytes("other/1"), DEFAULT_SETTINGS)
    assert unknown.status == "invalid" and "not accepted" in unknown.detail


def test_pdf_pages_with_label_values_and_a_table():
    result = analyze_asset("application/pdf", pdf_bytes(), DEFAULT_SETTINGS)
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
    assert analyze_asset(mime, data, DEFAULT_SETTINGS).status == status


def test_disabled_analyzers_and_page_limits_follow_the_settings():
    doc = DEFAULT_SETTINGS.model_dump(mode="json")
    doc["analyzers"]["chat"]["enabled"] = False
    doc["analyzers"]["pdf"]["max_pages"] = 1
    settings = CrawlerSettings.model_validate(doc)
    assert analyze_asset("application/json", chat_bytes(), settings).status == "unsupported"
    two_pages = pdf_bytes(REPORT_LINES + ["x"] * 60)
    result = analyze_asset("application/pdf", two_pages, settings)
    assert len(result.segments) == 1 and result.detail.startswith("read 1 of ")
