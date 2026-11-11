"""Decide where every file should go. Pure planning: reads the disk, never writes it.

A plan is the dry run. `--apply` hands the same plan to the executor.
"""

from __future__ import annotations

import os
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import naming, safety
from .models import Action, ActionKind, DuplicateGroup, DuplicateResult, FileRecord, ScanResult
from .rules import Rules

QUARANTINE_DIR = "_duplicates"  # copies go here, never straight to deletion
STATE_DIR = ".file-engine"  # undo journals live here


@dataclass(slots=True)
class Plan:
    root: Path
    dest: Path
    scan: ScanResult
    duplicates: DuplicateResult
    actions: list[Action] = field(default_factory=list)
    unchanged: int = 0
    category_files: Counter = field(default_factory=Counter)
    category_bytes: Counter = field(default_factory=Counter)
    refused: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def moves(self) -> list[Action]:
        return [a for a in self.actions if a.kind is ActionKind.ORGANIZE]

    @property
    def quarantines(self) -> list[Action]:
        return [a for a in self.actions if a.kind is ActionKind.QUARANTINE]

    @property
    def renames(self) -> int:
        return sum(1 for a in self.moves if a.src.name != a.dst.name)


@dataclass(slots=True)
class _Wanted:
    record: FileRecord
    kind: ActionKind
    target: Path
    stem: str
    ext: str
    category: str
    group: DuplicateGroup | None = None


def collision_key(path: Path) -> str:
    """Two paths that the SAME disk could treat as one file get the same key.
    macOS and Windows ignore case by default, and macOS treats 'é' and
    'e + combining accent' as the same name."""
    return unicodedata.normalize("NFC", str(path)).casefold()


def resolve_collision(target: Path, stem: str, ext: str, claimed: set[str]) -> Path:
    """First free name among target, stem-2.ext, stem-3.ext, ..."""
    candidate, n = target, 1
    while collision_key(candidate) in claimed or os.path.lexists(candidate):
        n += 1
        candidate = target.with_name(naming.fit_name(stem, ext, f"-{n}"))
    return candidate


def build_plan(
    scan: ScanResult,
    duplicates: DuplicateResult,
    *,
    dest: Path,
    rules: Rules,
    rename: bool,
    template: str = naming.DEFAULT_TEMPLATE,
) -> Plan:
    dest = dest.resolve()
    plan = Plan(root=scan.root, dest=dest, scan=scan, duplicates=duplicates)
    copy_of = {c.path: g for g in duplicates.groups for c in g.copies}

    wanted: list[_Wanted] = []
    for rec in scan.files:
        category = rules.category(rec.path.name)
        plan.category_files[category] += 1
        plan.category_bytes[category] += rec.size
        group = copy_of.get(rec.path)
        if group is not None:
            # keep the original folder structure inside _duplicates/ so a person
            # can see exactly where each copy came from
            target = dest / QUARANTINE_DIR / rec.path.relative_to(scan.root)
            stem, ext = naming.split_name(rec.path.name, rules)
            wanted.append(_Wanted(rec, ActionKind.QUARANTINE, target, stem, ext, category, group))
            continue
        if rename:
            stem, ext = naming.clean_name(rec, rules, template, category)
        else:
            stem, ext = naming.split_name(rec.path.name, rules)
        name = naming.fit_name(stem, ext)
        wanted.append(_Wanted(rec, ActionKind.ORGANIZE, dest / category / name, stem, ext, category))

    # Pass 1: files already in the right place keep it. Without this, a file
    # that is processed earlier could take a stayer's name and push it to "-2".
    claimed: set[str] = set()
    pending: list[_Wanted] = []
    for w in wanted:
        if w.target == w.record.path:
            claimed.add(collision_key(w.target))
            plan.unchanged += 1
        else:
            pending.append(w)

    # Pass 2: everyone else gets the first free name.
    for w in pending:
        target = resolve_collision(w.target, w.stem, w.ext, claimed)
        try:
            if not safety.is_safe_component(target.name):
                raise safety.UnsafePathError(f"unsafe name {target.name!r}")
            safety.ensure_inside(target, dest)
        except safety.UnsafePathError as exc:
            plan.refused.append((w.record.path, str(exc)))
            continue
        claimed.add(collision_key(target))
        plan.actions.append(
            Action(
                kind=w.kind,
                src=w.record.path,
                dst=target,
                size=w.record.size,
                mtime=w.record.mtime,
                category=w.category,
                digest=w.group.digest if w.group else None,
                keeper=w.group.keeper.path if w.group else None,
            )
        )
    # Quarantines first: each one re-reads its keeper at the keeper's CURRENT
    # path, so no keeper may be moved until every copy of it is dealt with.
    plan.actions.sort(key=lambda a: a.kind is not ActionKind.QUARANTINE)
    return plan
