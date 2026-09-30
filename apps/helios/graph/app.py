"""
Cloudera AI Workbench Application entry point for Helios Graph.

Create the Application with:
  Script:     helios/apps/helios/graph/app.py   (relative to the project root)
  Subdomain:  helios-graph
  Runtime:    helios-graph (PBJ Workbench, Python 3.11, Memgraph + Bolt driver)
  Environment: HELIOS_GRAPH_TOKEN  required bearer token the Helios API sends
  Leave "Unauthenticated Access" OFF; only the Helios API calls this.

PBJ Workbench executes this file inside a Jupyter kernel: __file__ is undefined and
an asyncio loop is already running, so uvicorn is started as a separate process.
Workbench routes traffic to 127.0.0.1:$CDSW_APP_PORT. Memgraph is a child of that
uvicorn process, on loopback Bolt only -- the proxy cannot forward Bolt anyway.

Health:  https://helios-graph.<workbench-domain>/health
Ready:   https://helios-graph.<workbench-domain>/ready
"""

import os
import subprocess
import sys

PROJECT_DIR = os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw")
ROOT = os.environ.get("HELIOS_ROOT") or os.path.join(PROJECT_DIR, "helios")
if not os.path.isdir(os.path.join(ROOT, "shared", "helios_core")):
    raise SystemExit(f"helios checkout not found at {ROOT}; set HELIOS_ROOT to the repo directory")

if not os.environ.get("HELIOS_GRAPH_TOKEN"):
    print(
        "WARNING: HELIOS_GRAPH_TOKEN is unset; /v1 endpoints will return 503 "
        "until it is set in the Application environment.",
        flush=True,
    )

port = os.environ.get("CDSW_APP_PORT", "8082")
cmd = [sys.executable, "-m", "uvicorn", "apps.helios.graph.gateway:app",
       "--host", "127.0.0.1", "--port", port, "--log-level", "info"]
print("starting helios graph gateway:", " ".join(cmd), flush=True)
python_paths = [
    os.path.join(ROOT, "shared"),  # shared core package location (first)
    ROOT,  # repo root, so apps.helios.graph resolves
]
dependency_dir = os.environ.get("HELIOS_PYTHON_DEPS") or os.path.join(
    PROJECT_DIR, ".helios-python"
)
if os.path.isdir(dependency_dir):
    python_paths.append(dependency_dir)
    print(f"using project-local Python dependencies: {dependency_dir}", flush=True)
existing_pythonpath = os.environ.get("PYTHONPATH")
if existing_pythonpath:
    python_paths.append(existing_pythonpath)
env = dict(os.environ, PYTHONPATH=os.pathsep.join(python_paths))
raise SystemExit(subprocess.call(cmd, cwd=ROOT, env=env))
