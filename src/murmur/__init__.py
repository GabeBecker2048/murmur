"""Murmur: does news predict prices, follow them, or move with them?"""

from importlib.metadata import PackageNotFoundError, version

# pyproject.toml is the only place the version is written (0.8.1's release failed
# because a second copy here was not bumped).
try:
    __version__ = version("murmur")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"
