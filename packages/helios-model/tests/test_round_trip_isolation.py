"""The round trip needs nothing but helios-cortex and helios-model (burndown 5.6).

import-linter proves helios-model's code names no console module. This proves it at run
time, plugins and lazy imports included: test_round_trip.py runs in a fresh interpreter
in which importing the console (``apps``), the Workbench Jobs (``jobs``), helios-cloudera
or the crawl code waiting in ``helios_cortex._pending`` fails, and every attempt is
recorded, so an import that something catches still counts. In the workspace all of
them are importable, so this is the check that matters there.
"""
import subprocess
import sys
from pathlib import Path

ROUND_TRIP = Path(__file__).resolve().parent / "test_round_trip.py"

RUNNER = r'''
import importlib.abc
import sys

BLOCKED = ("apps", "jobs", "helios_cloudera", "helios_cortex._pending")
attempts = []


def blocked(name):
    return any(name == b or name.startswith(b + ".") for b in BLOCKED)


class Refuse(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if blocked(name):
            attempts.append(name)
            raise ImportError(f"the round trip must not import {name}")
        return None


sys.meta_path.insert(0, Refuse())
import pytest  # noqa: E402

status = pytest.main(sys.argv[1:])
print("ATTEMPTS", sorted(set(attempts)))
print("LOADED", sorted(m for m in sys.modules if blocked(m)))
sys.exit(status)
'''


def test_the_round_trip_imports_no_console_jobs_or_plugin(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")        # no repository conftest or settings
    result = subprocess.run(
        [sys.executable, "-c", RUNNER, str(ROUND_TRIP), "-q", "-p", "no:cacheprovider",
         "-c", str(tmp_path / "pytest.ini"), "--rootdir", str(tmp_path)],
        cwd=tmp_path, capture_output=True, text=True, timeout=300)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "1 passed" in output, output
    assert "ATTEMPTS []" in output and "LOADED []" in output, output
