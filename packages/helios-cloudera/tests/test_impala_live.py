"""helios-cloudera's Impala engine against a real Impala warehouse: the plugin's live test,
as test_trino_live.py is the Trino engine's (burndown 8.5; CI's nightly cloudera-live job).

Runs when HELIOS_TEST_IMPALA names an Impala virtual warehouse (a host, or the JDBC URL
Cloudera shows) holding the TPC-DS database `tpcds` that setup/ builds, with a workload
user that can read it:

    HELIOS_TEST_IMPALA='jdbc:impala://<vw-host>:443/default;transportMode=http;httpPath=cliservice'
    HELIOS_TEST_IMPALA_USER=<workload user>  HELIOS_TEST_IMPALA_PASSWORD=<workload password>
    HELIOS_TEST_IMPALA_DELEGATE=<a user the workload user may impersonate; optional>
    uv run pytest packages/helios-cloudera/tests/test_impala_live.py

The engine is built from settings by its plugin, and the repository's published TPC-DS
model is compiled to Impala SQL by helios-cortex's compiler; every answer is checked
against SQL written by hand on the same warehouse. What the unit tests stand in for is
what this checks: impyla, Impala's dialect, and doAs with the effective-user check. The
facade over a registry is engine-independent and tested on DuckDB (tests/integration).
"""
import os
from pathlib import Path

import pytest

from helios_cortex import Caller
from helios_cortex.compiler import Compiler, Filter, SemanticRequest
from helios_cortex.engines import engine_for
from helios_cortex.ossie import SemanticModel
from helios_cortex.settings import Settings

SERVER = os.environ.get("HELIOS_TEST_IMPALA")
DELEGATE = os.environ.get("HELIOS_TEST_IMPALA_DELEGATE")
pytestmark = pytest.mark.skipif(not SERVER, reason="set HELIOS_TEST_IMPALA (and its user and password) "
                                                   "to run against Impala")
MODEL = Path(__file__).resolve().parents[3] / "models/published/tpcds.ossie.yaml"

BY_YEAR = ("SELECT d.d_year, SUM(s.ss_net_paid) FROM tpcds.store_sales s "
           "JOIN tpcds.date_dim d ON s.ss_sold_date_sk = d.d_date_sk GROUP BY 1")
BY_STATE_2001 = ("SELECT st.s_state, SUM(s.ss_net_paid), SUM(s.ss_quantity) FROM tpcds.store_sales s "
                 "JOIN tpcds.date_dim d ON s.ss_sold_date_sk = d.d_date_sk "
                 "JOIN tpcds.store st ON s.ss_store_sk = st.s_store_sk WHERE d.d_year = 2001 GROUP BY 1")


def impala(identity="service"):
    return engine_for(Settings(connections={"warehouse": {
        "engine": "impala", "host": SERVER, "identity": identity,
        "user": os.environ["HELIOS_TEST_IMPALA_USER"],
        "password": os.environ["HELIOS_TEST_IMPALA_PASSWORD"]}}).connection("warehouse"))


def compiled(engine, request):
    return Compiler(SemanticModel.load(str(MODEL))).compile(request, dialect=engine.dialect).sql


def test_the_engine_connects_through_its_plugin():
    engine = impala()
    assert type(engine).__name__ == "ImpalaEngine" and engine.ping()
    assert engine.query("SELECT COUNT(*) FROM tpcds.date_dim").rows[0][0] > 0


def test_compiled_tpcds_metrics_answer_as_the_hand_written_sql_does():
    engine = impala()
    by_year = engine.query(compiled(engine, SemanticRequest(metrics=["Store Sales Revenue"],
                                                            dimensions=["tpcds.date_dim.d_year"])), limit=None)
    assert by_year.rows, "the TPC-DS warehouse has no store sales"
    assert sorted(by_year.rows) == sorted(engine.query(BY_YEAR, limit=None).rows)

    request = SemanticRequest(metrics=["Store Sales Revenue", "Store Sales Quantity"],
                              dimensions=["tpcds.store.s_state"],
                              filters=[Filter("tpcds.date_dim.d_year", "=", 2001)])
    by_state = engine.query(compiled(engine, request), limit=None)
    assert sorted(by_state.rows) == sorted(engine.query(BY_STATE_2001, limit=None).rows)


@pytest.mark.skipif(not DELEGATE, reason="set HELIOS_TEST_IMPALA_DELEGATE to a user the workload user may impersonate")
def test_impersonation_runs_the_query_as_the_caller():
    engine, caller = impala("impersonate"), Caller(DELEGATE)
    assert [row[0].lower() for row in engine.query("SELECT effective_user()", caller).rows] == [DELEGATE.lower()]
    request = SemanticRequest(metrics=["Store Sales Revenue"], dimensions=["tpcds.date_dim.d_year"])
    assert engine.query(compiled(engine, request), caller, limit=None).rows
    assert engine.query("SELECT effective_user()").rows[0][0].lower() != DELEGATE.lower()  # no caller: the service user
