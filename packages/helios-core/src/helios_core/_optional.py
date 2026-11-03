"""Optional dependencies: the base install is the compiler, semantic model, DuckDB and
SQLite; anything else comes with an extra (``pip install 'helios-core[llm]'``).

A module that needs an extra raises ``missing_extra(...)`` from the ImportError, so
the message says what to install instead of naming a package the user never chose.
Base modules import those modules only where they are used.
"""
from __future__ import annotations


def missing_extra(feature: str, extra: str, error: ImportError) -> ModuleNotFoundError:
    """The error for ``feature`` when the package ``error`` failed to import is missing."""
    return ModuleNotFoundError(
        f"{feature} needs the optional dependency {error.name or 'it imports'}: "
        f"pip install 'helios-core[{extra}]'",
        name=error.name,
    )
