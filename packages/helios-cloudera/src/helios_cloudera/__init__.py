"""Helios plugins for Cloudera: the Impala engine and the Atlas catalog.

Installing this package registers ``engine = "impala"`` (helios_cloudera.impala)
and ``catalog = "atlas"`` (helios_cloudera.atlas) with helios-core's plugin loader.
"""


def __getattr__(name: str) -> str:
    # __version__ is read from the installed metadata on first use, so importing
    # helios_cloudera opens no files.
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("helios-cloudera")
        except PackageNotFoundError:  # running from a source path without an install
            return "0.0.0+unknown"
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
