#!/usr/bin/env python
"""Helios-DS-Generation FastAPI application startup script.

Cloudera AI Application entrypoint for the Helios-DS-Generation service.
Serves both the REST API and the web dashboard.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
DIST = ROOT / "apps" / "helios-ds-generation" / "ui" / "dist"

if __name__ == "__main__":
    if not DIST.exists():
        print(f"ERROR: UI dist directory not found at {DIST}")
        print("Please run 'npm run build' in apps/helios-ds-generation/ui/")
        sys.exit(1)

    os.chdir(ROOT)

    PYTHONPATH = [
        str(ROOT / "shared"),
        str(ROOT),
    ]

    env = os.environ.copy()
    env["PYTHONPATH"] = ":".join(PYTHONPATH)

    # Start FastAPI server with uvicorn
    subprocess.run([
        sys.executable,
        "-m",
        "uvicorn",
        "apps.helios_ds_generation.api.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ], env=env)
