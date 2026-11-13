"""The sample the integration tests share: what `helios init --sample` builds (burndown 9.1),
the TPC-DS store-sales star in DuckDB with its model registered and published. Built once
per run; the tests only read it. ``warehouse`` is the DuckDB file, ``settings`` the
project's settings (its helios.toml)."""
import pytest

from helios_cortex import sample as helios_sample
from helios_cortex.settings import Settings


@pytest.fixture(scope="session")
def sample(tmp_path_factory):
    return helios_sample.create(tmp_path_factory.mktemp("sample") / "helios.toml")


@pytest.fixture(scope="session")
def warehouse(sample):
    return sample.warehouse


@pytest.fixture
def settings(sample):
    return Settings.from_file(sample.config)
