"""Helios: build semantic models of your data and query them (burndown 6.6).

    from helios_semantic import Helios

    helios = Helios.from_settings()                 # helios.toml, HELIOS_* variables, defaults
    helios.models()
    helios.query(["revenue"], ["shop.customers.c_region"]).records()

``Helios`` is a facade over the libraries this package installs: helios-cortex (settings,
compiler, engines, registry, the ``helios`` command), helios-model (discovery and
publishing) and helios-serve (the tools). Extras add the rest:
``pip install "helios-semantic[cloudera,llm]"``.
"""

_EXPORTS = {name: "facade" for name in ("Helios", "HeliosError", "CompiledQuery", "QueryResult")}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    # Exports are imported on first use, so importing helios_semantic loads nothing heavy.
    if name in _EXPORTS:
        from importlib import import_module

        return getattr(import_module(f"helios_semantic.{_EXPORTS[name]}"), name)
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("helios-semantic")
        except PackageNotFoundError:  # running from a source path without an install
            return "0.0.0+unknown"
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
