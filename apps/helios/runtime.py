"""Process-wide configuration and stores for the pre-split apps (console, MCP server).

Built once at import, as helios_core.runs used to resolve its directories, so the
apps share one view of where runs and artifacts live. Code reads these attributes
at call time (``runtime.runs.load(...)``), which lets tests replace them in one place.
The console becomes an ordinary host of the libraries in burndown Phase 8.
"""
from pathlib import Path

from helios_core.artifacts import ArtifactStore
from helios_core.runs import RunStore
from helios_core.settings import Settings

# Files that ship in this repository (docs, ontology/, eval/), not runtime state:
# runtime state lives under settings.data_dir.
REPO = Path(__file__).resolve().parents[2]

settings = Settings()
runs = RunStore.from_settings(settings)
artifacts = ArtifactStore.from_settings(settings)
