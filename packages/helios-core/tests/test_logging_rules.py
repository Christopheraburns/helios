"""Library code logs through named loggers and leaves handlers to the host (burndown 1.11).

Every file under packages/*/src is parsed. ``print`` is allowed only in the command-line
tools, whose output is their job; 5.5 turns them into ``helios`` subcommands.
"""
import ast
from pathlib import Path

PACKAGES = Path(__file__).resolve().parents[2]
COMMANDS = {
    Path("helios-core/src/helios_core/engines/impala_diagnose.py"),
    Path("helios-core/src/helios_core/index/__main__.py"),
    Path("helios-core/src/helios_core/metadata/repair.py"),
    Path("helios-core/src/helios_core/metadata/setup.py"),
    Path("helios-core/src/helios_core/metadata/users.py"),
}
CONFIGURES_LOGGING = {"basicConfig", "dictConfig", "fileConfig", "addHandler", "setLevel"}
HANDLERS = {"StreamHandler", "FileHandler", "RotatingFileHandler", "TimedRotatingFileHandler"}


def problems(source: str, *, may_print: bool = False) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Name) and node.id == "print" and not may_print:
            found.append(f"{line}: print (use a module logger)")
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in CONFIGURES_LOGGING or name in HANDLERS:
                found.append(f"{line}: {name} (the host configures logging)")
            if name == "getLogger" and not (
                len(node.args) == 1 and isinstance(node.args[0], ast.Name) and node.args[0].id == "__name__"
            ):
                found.append(f"{line}: getLogger needs __name__")
    return found


def test_library_code_follows_the_logging_rules():
    offenders = [
        f"packages/{path.relative_to(PACKAGES)}:{problem}"
        for path in sorted(PACKAGES.glob("*/src/**/*.py"))
        for problem in problems(path.read_text(), may_print=path.relative_to(PACKAGES) in COMMANDS)
    ]
    assert not offenders, "\n".join(offenders)


def test_the_rules_catch_each_mistake():
    assert problems("import logging\nLOGGER = logging.getLogger(__name__)\nLOGGER.info('x')\n") == []
    assert problems("def run(log=print):\n    log('x')\n") == ["1: print (use a module logger)"]
    assert problems("print('x')\n", may_print=True) == []
    assert problems("import logging\nlogging.getLogger('helios')\n") == ["2: getLogger needs __name__"]
    assert problems("import logging\nlogging.basicConfig()\n") == ["2: basicConfig (the host configures logging)"]
    assert problems("import logging\nlogging.getLogger(__name__).addHandler(logging.StreamHandler())\n") == [
        "2: addHandler (the host configures logging)",
        "2: StreamHandler (the host configures logging)",
    ]
