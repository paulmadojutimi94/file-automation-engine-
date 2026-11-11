"""Shared helpers for the evals."""

from __future__ import annotations

import hashlib
import json
import platform
import statistics
import sys
from pathlib import Path

RESULTS = Path(__file__).parent / "results"
SKIP_PARTS = {".file-engine"}


def manifest(root: Path, *, with_mtime: bool = False) -> dict[str, tuple]:
    """Every entry under root -> (kind, sha256 or link target[, mtime]).
    Two equal manifests mean the trees are byte-for-byte the same."""
    out = {}
    for p in sorted(root.rglob("*")):
        if SKIP_PARTS & set(p.relative_to(root).parts):
            continue
        rel = str(p.relative_to(root))
        st = p.lstat()
        if p.is_symlink():
            out[rel] = ("link", str(p.readlink()))
        elif p.is_dir():
            # a folder's mtime changes whenever a file enters or leaves it, so
            # it can't survive a move-and-undo; compare existence only
            out[rel] = ("dir", "")
        else:
            entry = ("file", hashlib.sha256(p.read_bytes()).hexdigest())
            out[rel] = (*entry, st.st_mtime_ns) if with_mtime else entry
    return out


def content_multiset(root: Path) -> list[str]:
    """Sorted list of every regular file's sha256: equal lists = no bytes lost or duplicated."""
    return sorted(v[1] for v in manifest(root).values() if v[0] == "file")


def pct(values: list[float], q: float) -> float:
    return statistics.quantiles(values, n=100, method="inclusive")[q - 1] if len(values) > 1 else values[0]


def machine() -> str:
    import os

    return f"{platform.system()} {platform.machine()}, {os.cpu_count()} CPUs, Python {sys.version.split()[0]}"


def save(name: str, data: dict) -> Path:
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"{name}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
