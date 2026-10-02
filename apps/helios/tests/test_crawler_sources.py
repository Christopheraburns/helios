"""DS-1, DS-4, DS-5: connectors for object stores and table rows, built from data
sources, crawled end to end into helios_index."""

import io
import json
from datetime import UTC, datetime

import duckdb
import pytest
from apps.helios.crawler.analyzers import analyze_asset
from apps.helios.crawler.connectors import (
    ObjectStoreConnector,
    TableRowsConnector,
    build,
)
from apps.helios.crawler.crawl import crawl
from crawler_samples import email_bytes, pdf_bytes
from helios_core.crawler.settings import DEFAULT_SETTINGS
from helios_core.crawler.sources import catalog, validate_scope
from helios_core.index.store import duckdb_index_store


class FakeS3:
    """list_objects_v2 (paged, 2 per page) and get_object over an in-memory bucket."""

    def __init__(self, objects):
        self.objects = dict(objects)
        self.gets = 0

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start : start + 2]
        more = start + 2 < len(keys)
        return {
            "Contents": [
                {
                    "Key": k,
                    "ETag": f'"{hash(self.objects[k]) & 0xFFFF:x}"',
                    "Size": len(self.objects[k]),
                    "LastModified": datetime(2026, 9, 1, tzinfo=UTC),
                }
                for k in page
            ],
            "IsTruncated": more,
            **({"NextContinuationToken": str(start + 2)} if more else {}),
        }

    def get_object(self, Bucket, Key):
        self.gets += 1
        return {"Body": io.BytesIO(self.objects[Key])}


OBJECTS = {
    "docs/returns/report.pdf": pdf_bytes(),
    "docs/returns/complaint.eml": email_bytes(),
    "docs/notes/readme.txt": b"Customer Angela Raymond called about RMA-6326426.\n",
    "docs/notes/huge.txt": b"x" * 2000,
    "docs/images/photo.png": b"\x89PNG\r\n",
    "other/secret.txt": b"outside the prefix",
}


def _crawl(index, connector, source_id):
    return crawl(
        index,
        connector,
        source_id,
        actor="srv",
        ontology_version="0.2.0",
        settings=None,
        settings_hash=DEFAULT_SETTINGS.content_hash(),
        crawler_settings=DEFAULT_SETTINGS,
        source_snapshot={"id": source_id},
    )


def test_catalog_lists_the_crawlable_connector_types_with_form_schemas():
    types = {t["connector"]: t for t in catalog()}
    assert set(types) == {"helios_ds", "object_store", "table_rows"}
    assert "bucket" in types["object_store"]["scope_schema"]["properties"]


@pytest.mark.parametrize(
    "connector, scope, message",
    [
        ("object_store", {"prefix": "x"}, "bucket"),
        (
            "table_rows",
            {"table": "orders", "key_columns": ["id"], "text_columns": ["t"]},
            "database.table",
        ),
        (
            "table_rows",
            {"table": "db.t", "key_columns": ["id; drop"], "text_columns": ["t"]},
            "column names",
        ),
        ("impala", {}, "not a crawlable"),
    ],
)
def test_scopes_are_validated(connector, scope, message):
    with pytest.raises(ValueError, match=message):
        validate_scope(connector, scope)


def test_object_store_lists_in_scope_keys_across_pages_and_skips_large_objects():
    s3 = FakeS3(OBJECTS)
    connector = ObjectStoreConnector(
        "src",
        validate_scope(
            "object_store",
            {"bucket": "b", "prefix": "docs/", "exclude": ["images/*"], "max_bytes": 1900},
        ),
        s3,
    )
    assets = {a.asset_id: a for a in connector.list_assets()}
    assert set(assets) == {
        "docs/returns/report.pdf",
        "docs/returns/complaint.eml",
        "docs/notes/readme.txt",
        "docs/notes/huge.txt",
    }
    assert assets["docs/returns/complaint.eml"].mime_type == "message/rfc822"
    assert assets["docs/notes/readme.txt"].version.startswith("etag:")
    assert connector.fetch(assets["docs/notes/huge.txt"]).status == "too_large"
    assert connector.test().ok


def test_object_store_crawl_end_to_end_and_incrementally():
    index = duckdb_index_store()
    index.ensure_tables()
    s3 = FakeS3(OBJECTS)
    connector = build(
        "src",
        "object_store",
        "S3 Object Store",
        {"bucket": "b", "prefix": "docs/", "max_bytes": 1900},
        cursor=lambda: None,
        s3_client_for=lambda name: s3,
    )
    first = _crawl(index, connector, "src")
    assert first.connector == "object_store" and first.source == "src"
    assert first.counts["analyzed"] == 3  # pdf, email, text
    assert first.counts["too_large"] == 1 and first.counts["unsupported"] == 1  # the png
    texts = {
        s.segment_type
        for s in index.read("helios_index.segments", {"crawl_run_id": first.crawl_run_id})
    }
    assert {"page", "email_body", "text"} <= texts
    gets = s3.gets
    second = _crawl(index, connector, "src")
    assert second.counts["carried_forward"] == 3 and s3.gets == gets  # nothing re-read
    assert second.counts["unsupported"] == 1  # the png keeps its reason, without a download


@pytest.fixture
def warehouse():
    con = duckdb.connect()
    con.execute("CREATE SCHEMA support")
    con.execute(
        "CREATE TABLE support.tickets (id INTEGER, region VARCHAR, subject VARCHAR, "
        "body VARCHAR, opened DATE)"
    )
    con.execute(
        "INSERT INTO support.tickets VALUES "
        "(1, 'west', 'Damaged kettle', 'The box was crushed.', DATE '2001-06-14'),"
        "(2, 'west', 'Late order', NULL, DATE '2001-06-15'),"
        "(3, 'east', 'Wrong size', 'Sent medium instead of large.', DATE '2001-06-16')"
    )
    return con


def test_table_rows_become_documents_with_one_segment_per_text_column(warehouse):
    scope = {
        "table": "support.tickets",
        "key_columns": ["id"],
        "text_columns": ["subject", "body"],
        "timestamp_column": "opened",
        "filters": {"region": "west"},
    }
    connector = TableRowsConnector("tickets", validate_scope("table_rows", scope), warehouse.cursor)
    sql, params = connector.query()
    assert sql.endswith("WHERE region = ? ORDER BY id LIMIT 100000") and params == ["west"]
    assets = connector.list_assets()
    assert [a.asset_id for a in assets] == ["id=1", "id=2"]
    assert assets[0].semantic_timestamp == "2001-06-14"
    fetched = connector.fetch(assets[0])
    result = analyze_asset(assets[0].mime_type, fetched.data, DEFAULT_SETTINGS)
    assert [(s.locator, s.text) for s in result.segments] == [
        ({"column": "subject"}, "Damaged kettle"),
        ({"column": "body"}, "The box was crushed."),
    ]
    assert result.segments[0].fields["row_key"] == {"id": 1}
    second = analyze_asset(assets[1].mime_type, connector.fetch(assets[1]).data, DEFAULT_SETTINGS)
    assert [s.locator for s in second.segments] == [{"column": "subject"}]  # NULL body skipped


def test_table_rows_crawl_end_to_end(warehouse):
    index = duckdb_index_store()
    index.ensure_tables()
    connector = build(
        "tickets",
        "table_rows",
        "impala",
        {"table": "support.tickets", "key_columns": ["id"], "text_columns": ["body"]},
        cursor=warehouse.cursor,
        s3_client_for=lambda name: None,
    )
    run = _crawl(index, connector, "tickets")
    assert run.counts["analyzed"] == 2 and run.counts["no_text"] == 1
    with pytest.raises(ValueError, match="'impala'"):
        build(
            "t",
            "table_rows",
            "S3 Object Store",
            {"table": "a.b", "key_columns": ["k"], "text_columns": ["t"]},
            cursor=lambda: None,
            s3_client_for=lambda n: None,
        )


def test_text_files_are_one_segment_and_respect_the_size_setting():
    doc = DEFAULT_SETTINGS.model_dump(mode="json")
    doc["analyzers"]["text"]["max_chars"] = 10
    from helios_core.crawler.settings import CrawlerSettings

    result = analyze_asset(
        "text/plain", b"Hello Midway store\r\nsecond line", CrawlerSettings.model_validate(doc)
    )
    assert result.segments[0].locator == {"part": "text"}
    assert result.segments[0].text == "Hello Midw" and "cut at 10" in result.detail
    assert json
