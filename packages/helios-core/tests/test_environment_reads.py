"""Library code reads the environment only through helios_core.settings (burndown 1.7).

Every package under packages/ is scanned, so new libraries inherit the rule. The
crawl code in helios_core/_pending moves out with helios-crawl and is exempt until then.
"""
import ast
from pathlib import Path

PACKAGES = Path(__file__).resolve().parents[2]
ALLOWED = {Path("helios-core/src/helios_core/settings.py")}
EXEMPT_DIRS = {Path("helios-core/src/helios_core/_pending")}
ENV_NAMES = {"environ", "environb", "getenv", "getenvb", "putenv", "unsetenv"}


def environment_reads(source: str) -> list[int]:
    """Line numbers that touch os.environ / os.getenv and friends, however imported."""
    tree = ast.parse(source)
    os_aliases = {"os"}
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            os_aliases |= {a.asname or a.name for a in node.names if a.name == "os"}
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            if any(a.name in ENV_NAMES for a in node.names):
                lines.append(node.lineno)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in ENV_NAMES
            and isinstance(node.value, ast.Name)
            and node.value.id in os_aliases
        ):
            lines.append(node.lineno)
    return sorted(lines)


def _library_sources():
    for path in sorted(PACKAGES.glob("*/src/**/*.py")):
        relative = path.relative_to(PACKAGES)
        if relative in ALLOWED or any(d in relative.parents for d in EXEMPT_DIRS):
            continue
        yield relative, path


def test_only_settings_reads_the_environment():
    offenders = [
        f"packages/{relative}:{line}"
        for relative, path in _library_sources()
        for line in environment_reads(path.read_text())
    ]
    assert not offenders, (
        "Read configuration through helios_core.settings.Settings, not the environment: "
        + ", ".join(offenders)
    )


def test_the_scan_sees_libraries_and_catches_every_spelling():
    scanned = {relative for relative, _ in _library_sources()}
    assert Path("helios-core/src/helios_core/runs.py") in scanned
    assert not any(EXEMPT_DIRS & set(relative.parents) for relative in scanned)

    assert environment_reads("import os\nos.environ.get('X')\n") == [2]
    assert environment_reads("import os as o\no.getenv('X')\n") == [2]
    assert environment_reads("from os import environ\n") == [1]
    assert environment_reads("from os import getenv as g\n") == [1]
    assert environment_reads("import os\nos.path.join('a')\n") == []
