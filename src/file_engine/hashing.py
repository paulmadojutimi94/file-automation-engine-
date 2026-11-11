"""Find files with identical content, reading as few bytes as possible.

Three sieves, cheapest first:
  1. size          free (the scanner already knows it)
  2. edge digest   SHA-256 of the first and last 8 KiB
  3. full digest   SHA-256 of every byte, only for files that survived 1 and 2
"""

from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from .models import DuplicateGroup, DuplicateResult, FileRecord, Skipped

EDGE = 8 * 1024  # bytes read from each end in sieve 2
CHUNK = 1024 * 1024  # read size for full hashing: big enough to be fast, small enough for RAM


class ChangedWhileReading(OSError):
    """The file's size no longer matches what the scanner saw."""


def full_digest(path: Path) -> tuple[str, int]:
    """SHA-256 of the whole file, streamed in 1 MiB chunks. Returns (hex, bytes_read)."""
    h = hashlib.sha256()
    n = 0
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def edge_digest(path: Path, size: int) -> tuple[str, int]:
    """SHA-256 of the first and last EDGE bytes.

    If the file is no bigger than 2*EDGE we read all of it, so this IS the
    full SHA-256 and sieve 3 can be skipped for it.
    """
    with path.open("rb") as f:
        if size <= 2 * EDGE:
            data = f.read()
            if len(data) != size:
                raise ChangedWhileReading(f"{path}: size changed since scan")
            return hashlib.sha256(data).hexdigest(), len(data)
        head = f.read(EDGE)
        f.seek(-EDGE, os.SEEK_END)
        tail = f.read(EDGE)
        if f.tell() != size:
            raise ChangedWhileReading(f"{path}: size changed since scan")
    h = hashlib.sha256(head)
    h.update(tail)
    return h.hexdigest(), len(head) + len(tail)


# "Copy of x", "x copy", "x copy 2", "x (1)", "x - Copy"
_COPY_NAME = re.compile(r"(?i)(^copy of |[\s_-]copy(\s*\d+)?$|\s\(\d+\)$)")


def keeper_key(record: FileRecord) -> tuple:
    """Sort key: the FIRST file in this order is the one we keep.

    Prefer a name that doesn't look like a copy, then the oldest file,
    then the shortest path, then alphabetical (so the choice is deterministic).
    """
    stem = record.path.stem
    return (bool(_COPY_NAME.search(stem)), record.mtime, len(str(record.path)), str(record.path))


def _make_group(digest: str, members: list[FileRecord]) -> DuplicateGroup:
    ordered = sorted(members, key=keeper_key)
    return DuplicateGroup(digest=digest, keeper=ordered[0], copies=tuple(ordered[1:]))


def find_duplicates(files: Iterable[FileRecord], *, min_size: int = 1) -> DuplicateResult:
    """Group files by identical content. Empty files are ignored by default
    (min_size=1): every empty file is 'identical' to every other, which is
    true and useless."""
    result = DuplicateResult()

    by_size: dict[int, list[FileRecord]] = defaultdict(list)
    for f in files:
        if f.size >= min_size:
            by_size[f.size].append(f)

    for size, same_size in sorted(by_size.items()):
        if len(same_size) < 2:
            continue  # a unique size can't have a twin: zero bytes read

        by_edge: dict[str, list[FileRecord]] = defaultdict(list)
        for f in same_size:
            try:
                digest, n = edge_digest(f.path, size)
            except OSError as exc:
                result.errors.append(Skipped(f.path, f"hash_failed ({exc})"))
                continue
            result.bytes_read += n
            result.files_edge_hashed += 1
            by_edge[digest].append(f)

        for edge, same_edge in by_edge.items():
            if len(same_edge) < 2:
                continue
            if size <= 2 * EDGE:  # the edge digest already covered every byte
                result.groups.append(_make_group(edge, same_edge))
                continue
            by_full: dict[str, list[FileRecord]] = defaultdict(list)
            for f in same_edge:
                try:
                    digest, n = full_digest(f.path)
                except OSError as exc:
                    result.errors.append(Skipped(f.path, f"hash_failed ({exc})"))
                    continue
                if n != size:
                    result.errors.append(Skipped(f.path, "hash_failed (size changed since scan)"))
                    continue
                result.bytes_read += n
                result.files_full_hashed += 1
                by_full[digest].append(f)
            for digest, members in by_full.items():
                if len(members) > 1:
                    result.groups.append(_make_group(digest, members))

    result.groups.sort(key=lambda g: (-g.wasted_bytes, str(g.keeper.path)))
    return result
