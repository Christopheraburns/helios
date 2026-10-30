"""CONTRACTS.md stays true: every Stable and Provisional name imports from its module."""
import importlib
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
ROW = re.compile(r"^\| `(helios_core[\w.]*)` \| (.+?) \| (Stable|Provisional|Planned) \|")


def contract_rows():
    for line in (PACKAGE / "CONTRACTS.md").read_text().splitlines():
        match = ROW.match(line)
        if match:
            module, names, level = match.groups()
            yield module, re.findall(r"`([A-Za-z_]\w*)`", names), level


def test_every_contract_name_imports_from_its_module():
    rows = [row for row in contract_rows() if row[2] != "Planned"]
    assert len(rows) >= 10, "CONTRACTS.md tables did not parse"
    missing = [
        f"{module}.{name}"
        for module, names, _ in rows
        for name in names
        if not hasattr(importlib.import_module(module), name)
    ]
    assert not missing, f"named in CONTRACTS.md but not importable: {missing}"


def test_package_ships_py_typed():
    assert (PACKAGE / "src" / "helios_core" / "py.typed").is_file()
