"""Command line: file-engine scan | organize | undo | generate."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import __version__, safety
from .engine import plan_directory
from .executor import apply_plan
from .naming import DEFAULT_TEMPLATE
from .report import human_bytes, write_reports
from .rules import Rules
from .undo import undo


def _say(text: str = "") -> None:
    print(text)


def _show(path: Path, base: Path) -> str:
    return safety.display(str(path.relative_to(base)) if path.is_relative_to(base) else str(path))


def _plan(args: argparse.Namespace):
    rules = Rules.from_json(args.rules) if getattr(args, "rules", None) else None
    return plan_directory(
        args.path,
        dest=getattr(args, "dest", None),
        rules=rules,
        rename=getattr(args, "rename", False),
        template=getattr(args, "template", DEFAULT_TEMPLATE),
        dedupe=not getattr(args, "no_dedupe", False),
        include_hidden=args.include_hidden,
        min_dup_size=args.min_dup_size,
        exclude=[Path(args.report_dir).resolve()],
    )


def cmd_scan(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    plan = _plan(args)
    paths = write_reports(plan, Path(args.report_dir))
    d = plan.duplicates
    _say(
        f"Scanned {len(plan.scan.files):,} files ({human_bytes(plan.scan.total_bytes)}) "
        f"in {time.perf_counter() - t0:.2f}s. Nothing was changed."
    )
    _say(
        f"Duplicates: {sum(len(g.copies) for g in d.groups):,} copies in {len(d.groups):,} groups, "
        f"{human_bytes(sum(g.wasted_bytes for g in d.groups))} reclaimable "
        f"(read {human_bytes(d.bytes_read)} to find them)."
    )
    for cat, n in plan.category_files.most_common():
        _say(f"  {n:6,}  {cat:<14} {human_bytes(plan.category_bytes[cat]):>10}")
    _say(f"Report: {paths['markdown']}")
    return 0


def cmd_organize(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    plan = _plan(args)
    base = plan.dest
    _say(
        f"Planned in {time.perf_counter() - t0:.2f}s: {len(plan.moves):,} moves "
        f"({plan.renames:,} renamed), {len(plan.quarantines):,} duplicates to quarantine, "
        f"{plan.unchanged:,} already in place, {len(plan.scan.skipped):,} skipped."
    )
    for a in plan.actions[: args.show]:
        _say(f"  {a.kind.value:<10} {_show(a.src, plan.root)}  ->  {_show(a.dst, base)}")
    if len(plan.actions) > args.show:
        _say(f"  ... {len(plan.actions) - args.show:,} more (see actions.csv)")
    for path, why in plan.refused:
        _say(f"  REFUSED    {_show(path, plan.root)}: {safety.display(why)}")

    if not args.apply:
        paths = write_reports(plan, Path(args.report_dir))
        _say(f"\nDRY RUN: nothing was changed. Review {paths['markdown']}, then re-run with --apply.")
        return 0

    result = apply_plan(plan)
    paths = write_reports(plan, Path(args.report_dir), result)
    counts = result.counts
    _say(
        f"\nApplied: {counts.get('moved', 0):,} moved, {counts.get('skipped', 0):,} skipped, "
        f"{counts.get('failed', 0):,} failed."
    )
    for o in result.outcomes:
        if o.status != "moved":
            _say(f"  {o.status.upper():<8} {_show(o.action.src, plan.root)}: {safety.display(o.detail)}")
    _say(f"Report: {paths['markdown']}")
    _say(f"Undo:   file-engine undo {safety.display(str(result.journal))} --apply")
    return 1 if counts.get("failed") else 0


def cmd_undo(args: argparse.Namespace) -> int:
    result = undo(args.journal, apply=args.apply)
    counts: dict[str, int] = {}
    for step in result.steps:
        counts[step.status] = counts.get(step.status, 0) + 1
        if step.status not in ("restored", "would_restore"):
            _say(f"  {step.status.upper():<10} {safety.display(str(step.dst))}: {safety.display(step.detail)}")
    _say(", ".join(f"{k}: {v}" for k, v in counts.items()) or "journal has no moves")
    if not args.apply:
        _say("DRY RUN: nothing was changed. Re-run with --apply to restore.")
    else:
        _say(f"Removed {result.dirs_removed} empty folder(s) the run had created.")
    return 1 if counts.get("failed") or counts.get("refused") else 0


def cmd_generate(args: argparse.Namespace) -> int:
    from .generator import generate

    key = generate(Path(args.out), seed=args.seed, n_files=args.files)
    _say(
        f"Wrote {key['stats']['files']:,} files ({human_bytes(key['stats']['bytes'])}) "
        f"with {key['stats']['duplicate_groups']} planted duplicate groups to {args.out}"
    )
    _say(f"Answer key: {key['stats']['answer_key']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="file-engine", description="Stop organising files by hand.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("path", help="folder to work on")
        sp.add_argument("--report-dir", default="reports", help="where reports go (default: ./reports)")
        sp.add_argument("--include-hidden", action="store_true", help="also handle dot-files")
        sp.add_argument(
            "--min-dup-size",
            type=int,
            default=1,
            metavar="BYTES",
            help="ignore duplicates smaller than this (default 1: skip empty files)",
        )

    sp = sub.add_parser("scan", help="inventory + duplicates report; changes nothing")
    common(sp)
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("organize", aliases=["organise"], help="sort into category folders (dry run unless --apply)")
    common(sp)
    sp.add_argument("--dest", help="organise into this folder instead of in place")
    sp.add_argument("--rename", action="store_true", help="clean up names using --template")
    sp.add_argument(
        "--template",
        default=DEFAULT_TEMPLATE,
        help="new-name pattern from {date} {name} {category} (default: %(default)s)",
    )
    sp.add_argument("--rules", help="JSON file mapping categories to extensions")
    sp.add_argument("--no-dedupe", action="store_true", help="don't quarantine duplicates")
    sp.add_argument("--show", type=int, default=15, help="how many planned actions to print")
    sp.add_argument("--apply", action="store_true", help="actually move files (writes an undo journal)")
    sp.set_defaults(func=cmd_organize)

    sp = sub.add_parser("undo", help="reverse an applied run (dry run unless --apply)")
    sp.add_argument("journal", help="the .jsonl journal printed by organize --apply")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_undo)

    sp = sub.add_parser("generate", help="make a messy test folder with an answer key")
    sp.add_argument("out")
    sp.add_argument("--seed", type=int, default=7)
    sp.add_argument("--files", type=int, default=1000)
    sp.set_defaults(func=cmd_generate)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        print(f"error: {safety.display(str(exc))}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
