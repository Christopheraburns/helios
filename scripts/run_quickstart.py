"""Run the README's quickstart verbatim (burndown 9.2): every fenced block right after a
`<!-- quickstart: ... -->` line, in order, in one empty directory. Shell blocks run with
bash (stopping at the first failing command), Python blocks with the Python on PATH,
so run it in the virtual environment the quickstart describes:

    python scripts/run_quickstart.py README.md [--limit 300] [--workdir DIR]

It fails when a block fails, when the README has no quickstart blocks, or when the blocks
take longer than --limit seconds together (the README promises a few minutes). Standard
library only: it runs where nothing but the quickstart is installed.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MARKER = re.compile(r"^<!--\s*quickstart\b.*-->\s*$")
FENCE = re.compile(r"^```(\w+)\s*$")
RUNNERS = {"sh": ["bash", "-euo", "pipefail", "-c"], "bash": ["bash", "-euo", "pipefail", "-c"],
           "python": [shutil.which("python") or sys.executable, "-c"]}


def blocks(markdown: str) -> list[tuple[str, str]]:
    """(language, code) for each fenced block that follows a quickstart marker."""
    lines = markdown.splitlines()
    found = []
    for i, line in enumerate(lines):
        if not MARKER.match(line):
            continue
        fence = FENCE.match(lines[i + 1]) if i + 1 < len(lines) else None
        if fence is None:
            raise ValueError(f"line {i + 1}: a quickstart marker must be followed by a fenced code block")
        end = next(j for j in range(i + 2, len(lines)) if lines[j].startswith("```"))
        language = fence.group(1)
        if language not in RUNNERS:
            raise ValueError(f"line {i + 2}: no runner for a {language!r} block")
        found.append((language, "\n".join(lines[i + 2:end]) + "\n"))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("readme", type=Path)
    parser.add_argument("--limit", type=float, default=300.0, help="seconds all the blocks may take together")
    parser.add_argument("--workdir", type=Path, help="where to run them (default: a new empty directory)")
    args = parser.parse_args(argv)
    found = blocks(args.readme.read_text())
    if not found:
        print(f"no quickstart blocks in {args.readme}", file=sys.stderr)
        return 1
    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="helios-quickstart-"))
    workdir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    for number, (language, code) in enumerate(found, start=1):
        print(f"--- quickstart block {number} ({language}) in {workdir}", flush=True)
        block_started = time.monotonic()
        completed = subprocess.run([*RUNNERS[language], code], cwd=workdir)
        print(f"--- block {number}: exit {completed.returncode} in {time.monotonic() - block_started:.1f}s", flush=True)
        if completed.returncode:
            return completed.returncode
    took = time.monotonic() - started
    print(f"--- the quickstart took {took:.1f}s (limit {args.limit:.0f}s)")
    if took > args.limit:
        print("the quickstart took longer than its limit", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
