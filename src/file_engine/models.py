"""Plain data objects passed between the stages. No logic, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FileRecord:
    """One regular file, as seen by the scanner."""

    path: Path
    size: int
    mtime: float


@dataclass(frozen=True, slots=True)
class Skipped:
    """Something the engine deliberately did not touch, and why."""

    path: Path
    reason: str


@dataclass(slots=True)
class ScanResult:
    root: Path
    files: list[FileRecord] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    dirs_seen: int = 0

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    """Files with identical content. The keeper stays; the copies are quarantined."""

    digest: str
    keeper: FileRecord
    copies: tuple[FileRecord, ...]

    @property
    def wasted_bytes(self) -> int:
        return self.keeper.size * len(self.copies)


@dataclass(slots=True)
class DuplicateResult:
    groups: list[DuplicateGroup] = field(default_factory=list)
    bytes_read: int = 0
    files_edge_hashed: int = 0
    files_full_hashed: int = 0
    errors: list[Skipped] = field(default_factory=list)


class ActionKind(StrEnum):
    ORGANIZE = "organize"
    QUARANTINE = "quarantine"


@dataclass(frozen=True, slots=True)
class Action:
    """One planned move. Nothing happens until the executor runs it."""

    kind: ActionKind
    src: Path
    dst: Path
    size: int
    mtime: float
    category: str
    digest: str | None = None  # quarantine only: content must still match at apply time
    keeper: Path | None = None  # quarantine only: the copy that stays behind
