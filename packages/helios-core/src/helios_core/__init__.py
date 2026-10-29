"""helios core library: shared clients and logic for jobs and applications."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("helios-core")
except PackageNotFoundError:  # running from a source path without an install
    __version__ = "0.0.0+unknown"
