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
ROOT = os.environ.get("HELIOS_ROOT") or os.path.join(os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios")
if not os.path.isdir(os.path.join(ROOT, "helios_core")):
    raise SystemExit(f"helios checkout not found at {ROOT}; set HELIOS_ROOT to the repo directory")
 
port = os.environ.get("CDSW_APP_PORT", "8080")
cmd = [sys.executable, "-m", "uvicorn", "apps.console.main:app",
       "--host", "127.0.0.1", "--port", port, "--log-level", "info"]
if os.environ.get("HELIOS_DEV") == "1":
    cmd.append("--reload")
 
print("starting helios console:", " ".join(cmd), flush=True)
env = dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""))
raise SystemExit(subprocess.call(cmd, cwd=ROOT, env=env))
