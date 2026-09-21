"""
Cloudera AI Workbench Application entry point for the helios console.

Create the Application with:
  Script:     apps/console/app.py
  Subdomain:  helios  (any)
  Runtime:    helios (PBJ Workbench, Python 3.12)
Workbench routes traffic to 127.0.0.1:$CDSW_APP_PORT.
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

import uvicorn  # noqa: E402

if __name__ == "__main__":
    port = int(os.environ.get("CDSW_APP_PORT", "8080"))
    uvicorn.run("apps.console.main:app", host="127.0.0.1", port=port, log_level="info")
