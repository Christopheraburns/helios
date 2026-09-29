"""
Cloudera AI Workbench Application entry point for Helios-DS-Generation.

Serves both the REST API and the web dashboard.

Create the Application with:
  Script:     helios/apps/helios-ds-generation/app.py   (relative to the project root)
  Runtime:    helios (PBJ Workbench, Python 3.12)

PBJ Workbench executes this file inside a Jupyter kernel: __file__ is undefined and an
asyncio loop is already running, so uvicorn is started as a separate process instead.
Workbench routes traffic to 127.0.0.1:$CDSW_APP_PORT.
"""
import os
import subprocess
import sys

# Repo root: HELIOS_ROOT if set, else the conventional checkout location in the project.
PROJECT_DIR = os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw")
ROOT = os.environ.get("HELIOS_ROOT") or os.path.join(PROJECT_DIR, "helios")
if not os.path.isdir(os.path.join(ROOT, "shared", "helios_core")):
    raise SystemExit(f"helios checkout not found at {ROOT}; set HELIOS_ROOT to the repo directory")

APP_DIR = os.path.join(ROOT, "apps", "helios-ds-generation")
DIST = os.path.join(APP_DIR, "ui", "dist")
if not os.path.isdir(DIST):
    print(f"WARNING: UI dist not found at {DIST}; serving API only.", flush=True)
    print("Run 'npm install && npm run build' in apps/helios-ds-generation/ui/", flush=True)

port = os.environ.get("CDSW_APP_PORT", "8080")
cmd = [sys.executable, "-m", "uvicorn", "helios_ds_generation.api.main:app",
       "--host", "127.0.0.1", "--port", port, "--log-level", "info"]
if os.environ.get("HELIOS_DEV") == "1":
    cmd.append("--reload")

print("starting helios-ds-generation:", " ".join(cmd), flush=True)
python_paths = [
    os.path.join(ROOT, "shared"),  # shared core package location (first)
    os.path.join(APP_DIR, "src"),  # helios_ds and helios_ds_generation packages
    ROOT,  # repo root for monorepo structure
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
