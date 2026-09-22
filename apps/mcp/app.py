"""
Cloudera AI Workbench Application entry point for the helios MCP server.

Create the Application with:
  Script:     helios/apps/mcp/app.py
  Subdomain:  helios-mcp
  Runtime:    helios (PBJ Workbench, Python 3.12)
  Enable "Unauthenticated Access" so an MCP client can reach it, and set HELIOS_MCP_TOKEN in the project
  environment so only clients that send `Authorization: Bearer <token>` get through.

Endpoint for clients:  https://helios-mcp.<workbench-domain>/mcp   (Streamable HTTP)
Health:                https://helios-mcp.<workbench-domain>/healthz

Same launch pattern as the console: uvicorn in a subprocess on 127.0.0.1:$CDSW_APP_PORT.
"""
import os
import subprocess
import sys

ROOT = os.environ.get("HELIOS_ROOT") or os.path.join(os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios")
if not os.path.isdir(os.path.join(ROOT, "helios_core")):
    raise SystemExit(f"helios checkout not found at {ROOT}; set HELIOS_ROOT to the repo directory")

port = os.environ.get("CDSW_APP_PORT", "8081")
cmd = [sys.executable, "-m", "uvicorn", "apps.mcp.server:app", "--host", "127.0.0.1", "--port", port, "--log-level", "info"]
print("starting helios mcp server:", " ".join(cmd), flush=True)
env = dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""))
raise SystemExit(subprocess.call(cmd, cwd=ROOT, env=env))
