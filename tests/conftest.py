import os
from pathlib import Path

import pytest


def write(path: Path, data: bytes | str = b"x", mtime: float | None = 1_750_000_000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def tree(root: Path) -> dict[str, bytes]:
    """Every regular file under root (ignoring the engine's journal folder) -> its bytes."""
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.is_symlink() and ".file-engine" not in p.parts
    }


@pytest.fixture
def make():
    return write


@pytest.fixture
def snapshot():
    return tree
