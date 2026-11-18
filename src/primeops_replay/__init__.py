"""Point-in-time reconciliation of broker executions against client allocations."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("primeops-replay")
except PackageNotFoundError:  # running from a source checkout without installing
    __version__ = "0+unknown"
