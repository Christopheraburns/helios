import json
from pathlib import Path

import pytest

TINY = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"


@pytest.fixture
def tiny_config_dict() -> dict:
    return json.loads((TINY / "config.json").read_text())


@pytest.fixture
def tiny_store_returns() -> list:
    return json.loads((TINY / "store_returns.json").read_text())
