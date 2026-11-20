"""``python -m helios_cortex.cli`` reports a mistake in one line, like the ``helios`` script.

Running a module with -m loads it a second time as ``__main__``; its CommandError would not
be the one the commands raise, and the error came out as a traceback (seen from Horus,
2026-11-20)."""
import subprocess
import sys


def test_python_dash_m_reports_a_mistake_in_one_line(tmp_path):
    r = subprocess.run([sys.executable, "-m", "helios_cortex.cli", "model", "export", "nothing"],
                       cwd=tmp_path, capture_output=True, text=True,
                       env={"HELIOS_DATA_DIR": str(tmp_path / "data"), "PATH": ""})
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
    assert r.stderr.strip().splitlines()[-1].startswith("helios: error: ")
