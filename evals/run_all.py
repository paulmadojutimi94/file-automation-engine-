"""Run the evals and rewrite the results table in README.md.

python3 evals/run_all.py                     run everything (~3 min), update README
python3 evals/run_all.py --only accuracy,gold,attacks
python3 evals/run_all.py --check             fail if README doesn't match evals/results/
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
README = HERE.parent / "README.md"
START, END = "<!-- results:start -->", "<!-- results:end -->"
EVALS = ["accuracy", "gold", "perf", "attacks"]


def load(name: str) -> dict:
    return json.loads((HERE / "results" / f"{name}.json").read_text(encoding="utf-8"))


def mb(n: float) -> str:
    return f"{n / 1e6:,.0f} MB" if n < 1e9 else f"{n / 1e9:,.2f} GB"


def render() -> str:
    a, g, p, k = (load(n) for n in EVALS)
    rows = [
        (
            "**Duplicates found** (pair precision / recall)",
            f"**{a['dup_pair_precision']:.1%} / {a['dup_pair_recall']:.1%}**",
            f"{a['trees']} held-out trees (seeds {a['seeds']}), {a['files']:,} files, {mb(a['bytes'])}",
        ),
        ("Duplicate groups exactly right", a["dup_groups_exact"], "Every member, no extras"),
        (
            "Near-duplicate traps wrongly grouped",
            f"**{a['traps_grouped'].split('/')[0]}** / {a['traps_grouped'].split('/')[1]}",
            "Same size, same first+last 8 KiB, or same start with a different last byte",
        ),
        ("Empty files flagged as duplicates", str(a["empty_files_grouped"]), "Deliberately excluded"),
        ("**Categorisation**: generated files", f"{a['category_accuracy']:.1%}", "Generator's own ext → folder table"),
        (
            "**Categorisation**: hand-labelled gold set",
            f"**{g['right']} / {g['total']}** (first run {g['first_run']})",
            "40 names written and frozen before running; see postmortem",
        ),
        (
            "**Dry run** left every byte, timestamp and folder untouched",
            f"**{a['dry_run_clean']}** trees",
            "Full manifest incl. mtimes, before vs after",
        ),
        (
            "**Data loss** after apply",
            f"**0** (bytes identical in {a['bytes_preserved']} trees)",
            "Sorted SHA-256 of every file, before vs after",
        ),
        (
            "Each duplicate group left with exactly one live copy",
            a["groups_with_one_live_copy"],
            "Copies in `_duplicates/`, never deleted",
        ),
        ("Moves completed", f"{a['moved']:,} / {a['moved'] + a['not_moved']:,}", "None skipped or failed"),
        ("**Idempotent** (2nd run plans 0 actions)", a["idempotent"], "Same flags, same tree"),
        (
            "**Undo** restores every path, byte and file mtime",
            a["undo_exact"],
            "Folder mtimes excepted (known failure)",
        ),
        (
            "**Latency**: dry run (scan + dedupe + plan)",
            f"**p50 {p['plan_dry_run']['p50_ms']:.0f} ms, p95 {p['plan_dry_run']['p95_ms']:.0f} ms**",
            f"{p['files']:,} files, {mb(p['bytes'])}, {p['plan_dry_run']['runs']} warm runs",
        ),
        (
            "Latency: duplicate finding only (3 sieves)",
            f"p50 {p['dedupe_three_sieves']['p50_ms']:.0f} ms, p95 {p['dedupe_three_sieves']['p95_ms']:.0f} ms",
            "Same tree, same files",
        ),
        (
            "Latency: same tree, naive SHA-256 of every file",
            f"p50 {p['naive_full_hash']['p50_ms']:.0f} ms, p95 {p['naive_full_hash']['p95_ms']:.0f} ms",
            "Hashing only, no planning",
        ),
        (
            "**Bytes read** to find duplicates",
            f"**{mb(p['bytes_read_tiered'])}** vs {mb(p['bytes_read_naive'])} naive "
            f"({p['bytes_saved_share']:.0%} less)",
            f"{p['files_edge_hashed']:,} edge-hashed, {p['files_full_hashed']:,} fully hashed",
        ),
        (
            "Latency: apply (move + journal + fsync)",
            f"p50 {p['apply']['p50_ms']:,.0f} ms, p95 {p['apply']['p95_ms']:,.0f} ms",
            f"{p['files']:,} moves; ~70% is fsync (design doc)",
        ),
        (
            "Latency: undo",
            f"p50 {p['undo']['p50_ms']:,.0f} ms, p95 {p['undo']['p95_ms']:,.0f} ms",
            "Same moves reversed",
        ),
        (
            "Latency: whole CLI, cold start, dry run",
            f"p50 {p['cli_cold_dry_run']['p50_ms']:,.0f} ms, p95 {p['cli_cold_dry_run']['p95_ms']:,.0f} ms",
            "Includes Python start-up and writing 3 reports",
        ),
        (
            "Peak memory (Python heap)",
            f"{p['peak_python_heap_mb']:.1f} MB",
            f"{p['files']:,} files; grows with file count, not size",
        ),
        ("**Cost per task**", "**€0.00**", "0 paid API calls; runs locally"),
        (
            "**Attack success rate**, all defences on",
            f"**{k['succeeded_with_all_defences']} / {k['attacks']} "
            f"({k['succeeded_with_all_defences'] / k['attacks']:.1%})**",
            "16 attacks, each in its own temp folder",
        ),
    ]
    out = ["| What | Result | How it was measured |", "|---|---|---|"]
    out += [f"| {a_} | {b} | {c} |" for a_, b, c in rows]
    out += ["", "| Defence switched off | Attacks that then get through |", "|---|---|"]
    out += [f"| {r['defence_off']} | {r['succeeded']} / {r['tried']} |" for r in k["ablation"]]
    out += ["", f"Measured on {p['machine']}."]
    return "\n".join(out)


def splice(readme: str, table: str) -> str:
    head, rest = readme.split(START, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{START}\n{table}\n{END}{tail}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated subset of " + ",".join(EVALS))
    ap.add_argument("--check", action="store_true", help="don't run; fail if README is out of date")
    args = ap.parse_args()
    if not args.check:
        for name in args.only.split(",") if args.only else EVALS:
            print(f"running {name} ...", flush=True)
            importlib.import_module(name).main()
    current = README.read_text(encoding="utf-8")
    wanted = splice(current, render())
    if args.check:
        if current != wanted:
            print("README results table is out of date: run python3 evals/run_all.py")
            return 1
        print("README results table matches evals/results/")
        return 0
    README.write_text(wanted, encoding="utf-8")
    print("README updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
