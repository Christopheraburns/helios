"""AuditSink: libraries emit structured records; the host decides where they go (burndown 2.5)."""
import logging

import pytest

from helios_core.audit_sink import (
    AuditRecord,
    LoggingAuditSink,
    record_safely,
    sql_fingerprint,
)

QUERY = AuditRecord(
    action="run_query",
    outcome="success",
    model="sales",
    caller="alice",
    row_count=12,
    sql_fingerprint=sql_fingerprint("SELECT 1"),
    duration_ms=4.2,
)


def test_the_logging_sink_writes_one_structured_line(caplog):
    with caplog.at_level(logging.INFO, logger="helios_core.audit_sink"):
        LoggingAuditSink().record(QUERY)
    [line] = caplog.records
    assert line.name == "helios_core.audit_sink"
    assert line.levelno == logging.INFO
    assert line.getMessage().startswith("run_query success model=sales caller=alice rows=12 sql=")
    assert line.helios_audit == QUERY.as_dict()
    assert line.helios_audit["sql_fingerprint"] == QUERY.sql_fingerprint


@pytest.mark.parametrize("outcome", ["denied", "error"])
def test_denials_and_errors_are_warnings(caplog, outcome):
    with caplog.at_level(logging.INFO, logger="helios_core.audit_sink"):
        LoggingAuditSink().record(AuditRecord("run_query", outcome, error_code="query_denied"))
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert "error=query_denied" in caplog.text


def test_a_failing_sink_never_fails_the_action(caplog):
    class Broken:
        def record(self, event):
            raise OSError("disk full")

    record_safely(Broken(), QUERY)
    assert "audit sink Broken failed to record run_query" in caplog.text


def test_the_fingerprint_ignores_whitespace_and_hides_the_sql():
    assert sql_fingerprint("SELECT  a\n FROM t") == sql_fingerprint("SELECT a FROM t")
    assert sql_fingerprint("SELECT a FROM t") != sql_fingerprint("SELECT b FROM t")
    assert "SELECT" not in sql_fingerprint("SELECT a FROM t")


def test_records_are_immutable_and_check_their_outcome():
    with pytest.raises(ValueError, match="outcome"):
        AuditRecord("run_query", "maybe")
    record = AuditRecord("run_query", "error", details={"stage": "compile"})
    with pytest.raises(TypeError):
        record.details["stage"] = "run"  # type: ignore[index]
