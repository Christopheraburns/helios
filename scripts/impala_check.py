"""Isolate Impala connectivity and proxy delegation from the MCP server.

Run in a Workbench Session (same project env vars as the MCP Application):
  PYTHONPATH=helios/packages/helios-core/src python helios/scripts/impala_check.py cburns
Locally: uv run python scripts/impala_check.py cburns
Prints each step's result or Impala's full error message.
"""
import sys
from dataclasses import replace

from helios_core import Caller, IdentityMode
from helios_core.engines import ImpalaEngine, impala_config
from helios_core.settings import Settings

delegate = sys.argv[1] if len(sys.argv) > 1 else None
cfg = impala_config(Settings())
if cfg is None:
    sys.exit("no Impala connection: set IMPALA_HOST, WORKLOAD_USER and WORKLOAD_PASSWORD, or a [connections.default] with engine = \"impala\"")
print(f"host={cfg.host}:{cfg.port} http_path={cfg.http_path} connect_as={cfg.user} "
      f"identity={cfg.identity.value} delegate_to={delegate}")
engine = ImpalaEngine(replace(cfg, identity=IdentityMode.IMPERSONATE))
steps = [("1 connect, no delegation", "SELECT user(), effective_user()", None)]
if delegate:
    steps.append(("2 connect with delegation", "SELECT user(), effective_user()", delegate))
    steps.append(("3 delegated read on tpcds", "SELECT count(*) FROM tpcds.date_dim", delegate))
for label, sql, user in steps:
    try:
        print(f"[ok]   {label}: {engine.query(sql, Caller(user) if user else None, limit=10).rows}")
    except Exception as exc:  # noqa: BLE001
        print(f"[fail] {label}: {type(exc).__name__}: {exc}")
        break
