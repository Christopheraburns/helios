"""What each package says about itself on PyPI (burndown 9.4). The names are settled (Q8);
the umbrella, helios-semantic, reaches every other package through its dependencies or an
extra; every Helios dependency has a lower bound, because helios-cortex and helios-model
0.1.0 on PyPI are older code that a newer package must never resolve to; and each package
has a readme (its PyPI page), says it is typed, and ships py.typed. CI's package jobs also
run `twine check --strict` on each package's wheel and sdist."""
import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

PACKAGES = Path(__file__).resolve().parents[2] / "packages"
NAMES = {"helios-cortex", "helios-model", "helios-serve", "helios-semantic", "helios-mcp", "helios-cloudera"}
ON_PYPI_FROM_OLDER_CODE = Version("0.1.0")


def project(name):
    return tomllib.loads((PACKAGES / name / "pyproject.toml").read_text())["project"]


def requirements(meta):
    listed = list(meta.get("dependencies", []))
    for extra in meta.get("optional-dependencies", {}).values():
        listed += extra
    return [Requirement(r) for r in listed]


def test_the_packages_are_the_settled_names():
    assert {p.name for p in PACKAGES.iterdir() if (p / "pyproject.toml").is_file()} == NAMES
    for name in NAMES:
        assert project(name)["name"] == name


@pytest.mark.parametrize("name", sorted(NAMES))
def test_each_package_is_ready_for_pypi(name):
    meta = project(name)
    assert Version(meta["version"]) > ON_PYPI_FROM_OLDER_CODE
    assert meta["readme"] == "README.md" and (PACKAGES / name / "README.md").is_file()
    assert meta["license"] == "Apache-2.0" and meta["requires-python"] == ">=3.11"
    assert "Typing :: Typed" in meta["classifiers"] and meta["keywords"]
    module = name.replace("-", "_")
    assert (PACKAGES / name / "src" / module / "py.typed").is_file()
    for requirement in requirements(meta):
        if requirement.name in NAMES and requirement.name != name:
            bounds = [Version(s.version) for s in requirement.specifier if s.operator == ">="]
            assert bounds and min(bounds) > ON_PYPI_FROM_OLDER_CODE, f"{name}: {requirement}"


def test_the_umbrella_reaches_every_package_and_all_means_all():
    meta = project("helios-semantic")
    required = {Requirement(r).name for r in meta["dependencies"]}
    extras = meta["optional-dependencies"]
    by_extra = {Requirement(r).name for name, listed in extras.items() if name != "all" for r in listed}
    assert required | by_extra >= NAMES - {"helios-semantic"}
    [everything] = extras["all"]
    assert set(re.search(r"\[(.*)\]", everything).group(1).split(",")) == set(extras) - {"all"}
