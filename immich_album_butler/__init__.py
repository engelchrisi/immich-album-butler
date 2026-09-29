"""immich-album-butler: keep Immich albums in order, by people, places and dates."""

import tomllib
from pathlib import Path


def _read_version() -> str:
    """Read from pyproject.toml, shipped alongside the package by install.sh --
    nothing here is ever pip-installed, so importlib.metadata has nothing to find."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        return tomllib.loads(pyproject.read_text())["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "0.0.0+unknown"


__version__ = _read_version()
