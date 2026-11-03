"""The helios_index tables on Impala.

python -m helios_cloudera.index init-tables   create the tables through Impala (Helios settings)

The DDL itself is ``python -m helios_core.index ddl``.
"""

from __future__ import annotations

import sys

from helios_core.index.store import IndexStore
from helios_core.index.tables import IMPALA
from helios_core.settings import Settings

from helios_cloudera.impala import ImpalaEngine, impala_config
from helios_cloudera.workbench import WorkbenchSettings


def impala_index_store(settings: Settings) -> IndexStore | None:
    """The Workbench store on the ``default`` Impala connection; None if not configured."""
    config = impala_config(settings)
    if config is None:
        return None
    return IndexStore(ImpalaEngine(config).connect, IMPALA)


def main(argv: list[str]) -> int:
    command = argv[0] if argv else ""
    if command == "init-tables":
        store = impala_index_store(WorkbenchSettings())
        if store is None:
            print("Impala is not configured (IMPALA_HOST, WORKLOAD_USER, WORKLOAD_PASSWORD).")
            return 2
        store.ensure_tables()
        print("helios_index tables are in place.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
