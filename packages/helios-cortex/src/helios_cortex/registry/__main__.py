"""Registry schema administration, for the database at Settings.registry_url.

python -m helios_cortex.registry upgrade   bring the schema to this helios-cortex's version
python -m helios_cortex.registry current   print the schema version the database is at

The same as ``helios registry upgrade`` and ``helios registry current``.
"""
from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    from helios_cortex.cli import main as helios

    return helios(["registry", *argv])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
