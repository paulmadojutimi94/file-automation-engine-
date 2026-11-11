"""Walk a folder tree and list every regular file in it, without following symlinks."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable
from pathlib import Path

from .models import FileRecord, ScanResult, Skipped

# Names that belong to tools, not people. Moving them breaks things.
ALWAYS_IGNORED = frozenset(
    {".git", ".hg", ".svn", "__pycache__", "node_modules", ".DS_Store", "Thumbs.db", "desktop.ini"}
)


def entry_stat(path: Path) -> os.stat_result:
    """lstat: describe the entry itself. A symlink is reported AS a symlink,
    never as whatever it points to. (The attack eval swaps this for stat().)"""
    return path.lstat()


def scan(root: Path | str, *, include_hidden: bool = False, exclude: Iterable[Path] = ()) -> ScanResult:
    root = Path(root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(root)
    excluded = {Path(p).resolve() for p in exclude}

    result = ScanResult(root=root)
    stack = [root]  # an explicit stack instead of recursion: no depth limit
    while stack:
        directory = stack.pop()
        result.dirs_seen += 1
        try:
            children = list(directory.iterdir())
        except OSError as exc:
            result.skipped.append(Skipped(directory, f"unreadable_dir ({exc.strerror})"))
            continue

        for child in children:
            name = child.name
            if name in ALWAYS_IGNORED:
                result.skipped.append(Skipped(child, "tool_file"))
                continue
            if child in excluded:
                continue  # the engine's own folders: silent, not "skipped"
            if not include_hidden and name.startswith("."):
                result.skipped.append(Skipped(child, "hidden"))
                continue
            try:
                st = entry_stat(child)
            except OSError as exc:
                result.skipped.append(Skipped(child, f"unreadable ({exc.strerror})"))
                continue

            if stat.S_ISLNK(st.st_mode):
                result.skipped.append(Skipped(child, "symlink"))
            elif stat.S_ISDIR(st.st_mode):
                stack.append(child)
            elif stat.S_ISREG(st.st_mode):
                result.files.append(FileRecord(child, st.st_size, st.st_mtime))
            else:
                result.skipped.append(Skipped(child, "special_file"))  # sockets, pipes, devices

    result.files.sort(key=lambda f: f.path)
    result.skipped.sort(key=lambda s: s.path)
    return result
