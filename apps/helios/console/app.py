"""
Cloudera AI Workbench Application entry point for the helios console.
 
Create the Application with:
  Script:     helios/apps/console/app.py   (relative to the project root)
  Subdomain:  helios  (any)
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
if not os.path.isdir(os.path.join(ROOT, "helios_core")):
    raise SystemExit(f"helios checkout not found at {ROOT}; set HELIOS_ROOT to the repo directory")
 
port = os.environ.get("CDSW_APP_PORT", "8080")
cmd = [sys.executable, "-m", "uvicorn", "apps.console.main:app",
       "--host", "127.0.0.1", "--port", port, "--log-level", "info"]
if os.environ.get("HELIOS_DEV") == "1":
    cmd.append("--reload")
 
print("starting helios console:", " ".join(cmd), flush=True)
python_paths = [ROOT]
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
