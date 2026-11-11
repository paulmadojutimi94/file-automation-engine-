"""The public entry point: scan -> find duplicates -> plan. One function."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from . import naming
from .hashing import find_duplicates
from .models import DuplicateResult
from .planner import QUARANTINE_DIR, STATE_DIR, Plan, build_plan
from .rules import Rules
from .scanner import scan


def plan_directory(
    root: Path | str,
    *,
    dest: Path | str | None = None,
    rules: Rules | None = None,
    rename: bool = False,
    template: str = naming.DEFAULT_TEMPLATE,
    dedupe: bool = True,
    include_hidden: bool = False,
    min_dup_size: int = 1,
    exclude: Iterable[Path | str] = (),
) -> Plan:
    """Work out what organising `root` would do. Changes nothing on disk."""
    naming.validate_template(template)
    root = Path(root).expanduser().resolve(strict=True)
    dest = Path(dest).expanduser().resolve() if dest else root
    skip = [dest / QUARANTINE_DIR, dest / STATE_DIR, *map(Path, exclude)]
    scanned = scan(root, include_hidden=include_hidden, exclude=skip)
    dups = find_duplicates(scanned.files, min_size=min_dup_size) if dedupe else DuplicateResult()
    return build_plan(scanned, dups, dest=dest, rules=rules or Rules(), rename=rename, template=template)
