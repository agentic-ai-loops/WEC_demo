"""Safe access to files under `businessdata/resources/` (01-businessdata, *Assets*).

An asset's `path` names a file inside the resources folder: relative, `/`-separated,
each segment starting with a letter, digit or `_` (so no `..`, no hidden files, no
absolute paths). `resource_file` also resolves symlinks, so a link pointing out of the
folder is refused.
"""

import re
from pathlib import Path

# One or more segments; a segment starts with [A-Za-z0-9_] and continues with those,
# `.`, `-` or spaces.
RESOURCE_PATH = r"^[A-Za-z0-9_][A-Za-z0-9_. -]*(/[A-Za-z0-9_][A-Za-z0-9_. -]*)*$"
MAX_PATH_LENGTH = 255
_PATTERN = re.compile(RESOURCE_PATH)


class UnsafePath(ValueError):
    """An asset path that would reach outside `businessdata/resources/`."""


def check_path(path: str) -> None:
    """Raise `UnsafePath` unless `path` is a well-formed resource path."""
    if len(path) > MAX_PATH_LENGTH or not _PATTERN.match(path):
        raise UnsafePath(
            f"path {path!r}: must be relative to businessdata/resources/, made of "
            "segments starting with a letter, digit or '_' (no '..', no leading '/')"
        )


def inside(path: Path, root: Path) -> bool:
    """Whether `path`, with symlinks resolved, is `root` or inside it."""
    return path.resolve().is_relative_to(root.resolve())


def resource_file(resources_dir: Path, path: str) -> Path:
    """The file an asset path names; `UnsafePath` if it would leave the folder.

    The file need not exist; callers check `is_file()`.
    """
    check_path(path)
    file = resources_dir / path
    if not inside(file, resources_dir):
        raise UnsafePath(f"path {path!r}: resolves outside businessdata/resources/")
    return file
