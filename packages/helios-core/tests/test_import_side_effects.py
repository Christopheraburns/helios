"""Importing helios_core reads no environment variables and opens no files (burndown 1.10).

Each check runs in a fresh interpreter. An audit hook sees every file open, and
os.environ is swapped for a recording mapping before anything is imported. An access
counts against helios_core when the nearest frame outside the standard library is
helios_core's own code, so helios_core calling open(), Path.read_text() or
importlib.metadata counts, while Python loading module source, or pydantic and other
dependencies reading their own settings, does not.
"""
import json
import subprocess
import sys
import textwrap

PROBE = textwrap.dedent(
    """
    import collections.abc, importlib, json, os, pkgutil, sys
    hits = []

    def culprit():
        frame = sys._getframe(2)
        while frame is not None:
            name = frame.f_code.co_filename
            package = frame.f_globals.get("__name__", "").partition(".")[0]
            if name.startswith("<frozen importlib"):
                return None  # the import system reading module source
            if package == "helios_core":
                return f"{name.split('/helios_core/', 1)[1]}:{frame.f_lineno}"
            if package not in sys.stdlib_module_names and package != "__main__":
                return None  # a dependency's own behaviour
            frame = frame.f_back
        return None

    def record(kind, detail):
        where = culprit()
        if where:
            hits.append(f"{kind} {detail} at helios_core/{where}")

    class RecordingEnviron(collections.abc.MutableMapping):
        def __init__(self, data):
            self._data = data
        def __getitem__(self, key):
            record("env", key)
            return self._data[key]
        def __setitem__(self, key, value):
            record("env-write", key)
            self._data[key] = value
        def __delitem__(self, key):
            record("env-write", key)
            del self._data[key]
        def __iter__(self):
            record("env", "<iterate>")
            return iter(self._data)
        def __len__(self):
            return len(self._data)
        def __contains__(self, key):
            record("env", key)
            return key in self._data
        def copy(self):
            return dict(self._data)

    os.environ = RecordingEnviron(os.environ)

    def audit(event, args):
        if event == "open":
            record("open", args[0])
    sys.addaudithook(audit)

    def modules(package):
        # Like pkgutil.walk_packages, without importing _pending to look inside it.
        yield package.__name__
        for info in pkgutil.iter_modules(package.__path__, package.__name__ + "."):
            if info.name.endswith(("._pending", ".__main__")):
                continue
            module = importlib.import_module(info.name)
            if info.ispkg:
                yield from modules(module)
            else:
                yield info.name

    import helios_core
    names = list(modules(helios_core))
    print(json.dumps({"hits": hits, "modules": names}))
    """
)


def probe(**kwargs) -> dict:
    result = subprocess.run(
        [sys.executable, "-c", PROBE], capture_output=True, text=True, check=True, **kwargs
    )
    return json.loads(result.stdout.splitlines()[-1])


def test_importing_helios_core_has_no_side_effects():
    report = probe()
    assert {"helios_core.settings", "helios_core.compiler.compiler", "helios_core.index.store"} <= set(
        report["modules"]
    )
    assert not any("._pending" in name for name in report["modules"])
    assert report["hits"] == []


def test_the_probe_catches_env_reads_and_file_opens(tmp_path):
    package = tmp_path / "helios_core"
    package.mkdir()
    (package / "__init__.py").write_text(
        "import os\nfrom pathlib import Path\nos.environ.get('HOME')\nPath(__file__).read_text()\n"
    )
    hits = probe(env={"PYTHONPATH": str(tmp_path), "HOME": "/nowhere", "PATH": "/usr/bin"})["hits"]
    assert [h.split(" at ")[0].split()[0] for h in hits] == ["env", "open"], hits
    assert all(h.endswith(("__init__.py:3", "__init__.py:4")) for h in hits), hits
