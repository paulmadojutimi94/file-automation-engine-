"""Reverse an applied run using its journal. Dry run unless apply=True."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import executor
from .planner import STATE_DIR


@dataclass(slots=True)
class UndoStep:
    src: Path  # where the file was before the run (where it goes back to)
    dst: Path  # where the run put it
    status: str  # "would_restore" | "restored" | "not_moved" | "missing" | "refused" | "failed"
    detail: str = ""


@dataclass(slots=True)
class UndoResult:
    journal: Path
    root: Path
    dest: Path
    applied: bool
    steps: list[UndoStep] = field(default_factory=list)
    dirs_removed: int = 0


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def check_entry(src: Path, dst: Path, root: Path, dest: Path) -> str | None:
    """A journal is just a text file; don't trust it to point anywhere it likes."""
    if not (src.is_absolute() and dst.is_absolute()):
        return "relative path in journal"
    if not _inside(dst, dest):
        return "moved-to path is outside the organised folder"
    if not _inside(src, root):
        return "restore path is outside the original folder"
    return None


def undo(journal_path: Path | str, *, apply: bool = False) -> UndoResult:
    journal_path = Path(journal_path).resolve()
    lines = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines() if line]
    if not lines or lines[0].get("op") != "header":
        raise ValueError(f"{journal_path} is not a file-engine journal")
    if any(e.get("op") == "undone" for e in lines):
        raise ValueError(f"{journal_path} was already undone")
    if journal_path.parent.name != STATE_DIR:
        raise ValueError(f"journal must sit in a {STATE_DIR}/ folder")

    # dest comes from WHERE the journal is, not from what it says
    dest = journal_path.parent.parent
    root = Path(lines[0]["root"])
    result = UndoResult(journal=journal_path, root=root, dest=dest, applied=apply)

    for entry in reversed([e for e in lines if e.get("op") == "move"]):
        src, dst = Path(entry["src"]), Path(entry["dst"])
        problem = check_entry(src, dst, root, dest)
        if problem:
            result.steps.append(UndoStep(src, dst, "refused", problem))
        elif os.path.lexists(src) and not os.path.lexists(dst):
            result.steps.append(UndoStep(src, dst, "not_moved", "the run skipped this file"))
        elif not os.path.lexists(dst):
            result.steps.append(UndoStep(src, dst, "missing", "file is no longer where the run put it"))
        elif os.path.lexists(src):
            result.steps.append(UndoStep(src, dst, "failed", "something new is at the original path"))
        elif not apply:
            result.steps.append(UndoStep(src, dst, "would_restore"))
        else:
            try:
                src.parent.mkdir(parents=True, exist_ok=True)
                executor.move_no_clobber(dst, src)
                result.steps.append(UndoStep(src, dst, "restored"))
            except OSError as exc:
                result.steps.append(UndoStep(src, dst, "failed", str(exc.strerror or exc)))

    if apply:
        for entry in reversed([e for e in lines if e.get("op") == "mkdir"]):
            d = Path(entry["path"])
            if _inside(d, dest) and d.is_dir() and not any(d.iterdir()):
                d.rmdir()
                result.dirs_removed += 1
        with journal_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"op": "undone"}) + "\n")
    return result
