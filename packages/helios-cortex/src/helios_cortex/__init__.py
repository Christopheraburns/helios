"""helios core library: shared clients and logic for jobs and applications."""

from helios_core.caller import Caller, IdentityError, IdentityMode

__all__ = ["Caller", "IdentityError", "IdentityMode"]


def __getattr__(name: str) -> str:
    # __version__ is read from the installed metadata on first use, so importing
    # helios_core opens no files.
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("helios-core")
        except PackageNotFoundError:  # running from a source path without an install
            return "0.0.0+unknown"
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
