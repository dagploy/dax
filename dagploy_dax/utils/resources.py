from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def package_file(package: str, relative_path: str) -> Path:
    """
    Return a filesystem path to a packaged resource.

    Example:
        package_file("dax", "config/default.yaml")
    """
    return Path(str(files(package).joinpath(relative_path)))


def dax_resource(relative_path: str) -> Path:
    return package_file("dax", relative_path)