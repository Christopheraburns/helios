"""The crawler's tests, until helios-crawl takes them (Phase 10); everything else in this
directory moved to the packages or went with the console (burndown 8.1)."""
import os
import tempfile

# Some crawler tests reach the console's crawler and ontology routers, and the console
# migrates a metadata database whose default lives under /home/cdsw. Point it somewhere
# disposable so the suite runs off Workbench too. Must run before any test imports the console.
os.environ.setdefault(
    "HELIOS_METADATA_DB",
    os.path.join(tempfile.mkdtemp(prefix="helios-tests-"), "helios.db"),
)
