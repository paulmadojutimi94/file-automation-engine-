"""Carry out a plan. The only module (with undo.py) that changes anything on disk.

Rules it never breaks:
  * never overwrite: a move fails rather than replace an existing file
  * never delete: duplicates are moved to _duplicates/, not removed
  * write the undo journal line BEFORE each move (write-ahead logging)
  * re-check every file just before touching it; the plan may be stale
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import stat
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import IO

from . import hashing, safety
from .models import Action, ActionKind
from .planner import STATE_DIR, Plan

JOURNAL_VERSION = 1


@dataclass(frozen=True, slots=True)
class Outcome:
    action: Action
    status: str  # "moved" | "skipped" | "failed"
    detail: str = ""


@dataclass(slots=True)
class ApplyResult:
    journal: Path
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def counts(self) -> Counter:
        return Counter(o.status for o in self.outcomes)


# ---------------------------------------------------------------- moving files


def _copy_no_clobber(src: Path, dst: Path) -> None:
    """Fallback when hard links aren't possible (e.g. another disk).
    Mode 'xb' = create, and fail if the file already exists."""
    try:
        with src.open("rb") as fin, dst.open("xb") as fout:
            shutil.copyfileobj(fin, fout, hashing.CHUNK)
        shutil.copystat(src, dst)  # keep the modified time, so dates don't change
        if dst.stat().st_size != src.stat().st_size:
            raise OSError(errno.EIO, "copy is incomplete", str(dst))
    except FileExistsError:
        raise
    except BaseException:
        dst.unlink(missing_ok=True)  # never leave a half-written copy behind
        raise
    src.unlink()


def move_no_clobber(src: Path, dst: Path) -> None:
    """Move src to dst, raising FileExistsError instead of overwriting.

    os.rename() would silently replace dst on Linux and macOS. os.link() is
    atomic and refuses if dst exists, so: link the new name, then remove the
    old one. Same disk only; otherwise copy with exclusive create.
    """
    try:
        os.link(src, dst, follow_symlinks=False)
    except FileExistsError:
        raise
    except OSError as exc:
        if exc.errno in (errno.EXDEV, errno.EPERM, errno.ENOTSUP, errno.EMLINK, errno.EACCES):
            _copy_no_clobber(src, dst)
            return
        raise
    os.unlink(src)


# ---------------------------------------------------------------- re-checks


def stale_reason(action: Action) -> str | None:
    """Has the file changed since we planned? Then don't touch it."""
    try:
        st = action.src.lstat()
    except FileNotFoundError:
        return "source no longer exists"
    if not stat.S_ISREG(st.st_mode):
        return "source is no longer a regular file"
    if st.st_size != action.size or st.st_mtime != action.mtime:
        return "source changed since the plan was made"
    return None


def verify_duplicate(action: Action) -> str | None:
    """Only quarantine a copy if it AND the keeper still hash to the same digest,
    so there is always a verified identical file left in place."""
    assert action.digest and action.keeper
    for path in (action.src, action.keeper):
        try:
            if path.is_symlink():
                return f"{path.name} is now a symlink"
            digest, _ = hashing.full_digest(path)
        except OSError as exc:
            return f"cannot re-read {path.name}: {exc.strerror}"
        if digest != action.digest:
            return f"{path.name} changed since scan; no longer a duplicate"
    return None


# ---------------------------------------------------------------- journal


def _log(journal: IO[str], entry: dict) -> None:
    journal.write(json.dumps(entry, ensure_ascii=False) + "\n")
    journal.flush()
    os.fsync(journal.fileno())  # on disk before we act, so a crash can be undone


def _make_dirs(directory: Path, dest: Path, journal: IO[str]) -> None:
    """mkdir -p, journaling each folder we create so undo can remove it."""
    missing = []
    d = directory
    while not d.exists() and d != dest:
        missing.append(d)
        d = d.parent
    for d in reversed(missing):
        d.mkdir()
        _log(journal, {"op": "mkdir", "path": str(d)})


# ---------------------------------------------------------------- apply


def _apply_one(action: Action, plan: Plan, journal: IO[str]) -> Outcome:
    reason = stale_reason(action)
    if reason:
        return Outcome(action, "skipped", reason)
    if action.kind is ActionKind.QUARANTINE:
        reason = verify_duplicate(action)
        if reason:
            return Outcome(action, "skipped", reason)
    try:
        safety.ensure_inside(action.dst, plan.dest)  # re-check: a folder may have become a symlink
        _make_dirs(action.dst.parent, plan.dest, journal)
        safety.ensure_inside(action.dst, plan.dest)
        _log(journal, {"op": "move", "src": str(action.src), "dst": str(action.dst)})
        move_no_clobber(action.src, action.dst)
    except FileExistsError:
        return Outcome(action, "skipped", "destination appeared after planning; not overwritten")
    except safety.UnsafePathError as exc:
        return Outcome(action, "failed", str(exc))
    except OSError as exc:
        return Outcome(action, "failed", f"{exc.strerror or exc}")
    return Outcome(action, "moved")


def apply_plan(plan: Plan) -> ApplyResult:
    state = plan.dest / STATE_DIR
    state.mkdir(parents=True, exist_ok=True)
    journal_path = state / f"journal-{datetime.now():%Y%m%d-%H%M%S-%f}.jsonl"
    result = ApplyResult(journal=journal_path)
    with journal_path.open("x", encoding="utf-8") as journal:
        _log(
            journal,
            {
                "op": "header",
                "version": JOURNAL_VERSION,
                "root": str(plan.root),
                "dest": str(plan.dest),
                "created": datetime.now().isoformat(timespec="seconds"),
            },
        )
        for action in plan.actions:
            result.outcomes.append(_apply_one(action, plan, journal))
    return result
