"""The helios_index tables on Impala.

python -m helios_cloudera.index init-tables   create the tables through Impala (Helios settings)

The DDL itself is ``python -m helios_cortex.index ddl``.
"""

from __future__ import annotations

import sys

from helios_cortex.index.store import IndexStore
from helios_cortex.index.tables import IMPALA
from helios_cortex.settings import Settings

from helios_cloudera.impala import ImpalaEngine, impala_config
from helios_cloudera.workbench import WorkbenchSettings


def impala_index_store(settings: Settings) -> IndexStore | None:
    """The Workbench store on the ``default`` Impala connection; None if not configured."""
    config = impala_config(settings)
    if config is None:
        return None
    return IndexStore(ImpalaEngine(config).connect, IMPALA)


def init_tables(settings: Settings) -> bool:
    """Create the helios_index tables on the ``default`` Impala connection; False when
    Impala is not configured."""
    store = impala_index_store(settings)
    if store is None:
        return False
    store.ensure_tables()
    return True


def main(argv: list[str]) -> int:
    """``python -m helios_cloudera.index``; the same as
    ``helios --profile workbench impala init-index``."""
    command = argv[0] if argv else ""
    if command == "init-tables":
        if not init_tables(WorkbenchSettings()):
            print("Impala is not configured (IMPALA_HOST, WORKLOAD_USER, WORKLOAD_PASSWORD).")
            return 2
        print("helios_index tables are in place.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
