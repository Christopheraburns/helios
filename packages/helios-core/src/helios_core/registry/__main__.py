"""Registry schema administration, for the database at Settings.registry_url.

python -m helios_core.registry upgrade   bring the schema to this helios-core's version
python -m helios_core.registry current   print the schema version the database is at
"""
from __future__ import annotations

import sys

from helios_core.registry.sql import SqlRegistry
from helios_core.settings import Settings


def main(argv: list[str]) -> int:
    command = argv[0] if argv else ""
    if command not in ("upgrade", "current"):
        print(__doc__)
        return 2
    registry = SqlRegistry.from_settings(Settings(), upgrade=False)
    if command == "upgrade":
        registry.upgrade()
    print(f"{registry!r}: schema {registry.current_revision() or 'empty'} (this helios-core: {registry.head_revision()})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
