"""
Cloudera AI Workbench Application entry point for the helios MCP server.

Create the Application with:
  Script:     helios/apps/mcp/app.py
  Subdomain:  helios-mcp
  Runtime:    standard PBJ Workbench Python 3.12
  Enable "Unauthenticated Access" so an MCP protocol client can reach it.
  HELIOS_MCP_TOKEN remains mandatory, and every tool also requires either a
  signed Principal assertion or an explicitly registered agent Principal.

Endpoint for clients:  https://helios-mcp.<workbench-domain>/mcp   (Streamable HTTP)
Health:                https://helios-mcp.<workbench-domain>/healthz

Same launch pattern as the console: uvicorn in a subprocess on 127.0.0.1:$CDSW_APP_PORT.
"""
import os
import subprocess
import sys

PROJECT_DIR = os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw")
ROOT = os.environ.get("HELIOS_ROOT") or os.path.join(
    PROJECT_DIR, "helios"
)
if not os.path.isdir(os.path.join(ROOT, "helios_core")):
    raise SystemExit(f"helios checkout not found at {ROOT}; set HELIOS_ROOT to the repo directory")

port = os.environ.get("CDSW_APP_PORT", "8081")
cmd = [sys.executable, "-m", "uvicorn", "apps.mcp.server:app", "--host", "127.0.0.1", "--port", port, "--log-level", "info"]
print("starting helios mcp server:", " ".join(cmd), flush=True)
python_paths = [ROOT]
dependency_dir = os.environ.get("HELIOS_PYTHON_DEPS") or os.path.join(
    PROJECT_DIR, ".helios-python"
)
if os.path.isdir(dependency_dir):
    python_paths.append(dependency_dir)
    print(
        f"using project-local Python dependencies: {dependency_dir}",
        flush=True,
    )
existing_pythonpath = os.environ.get("PYTHONPATH")
if existing_pythonpath:
    python_paths.append(existing_pythonpath)
env = dict(os.environ, PYTHONPATH=os.pathsep.join(python_paths))
raise SystemExit(subprocess.call(cmd, cwd=ROOT, env=env))
