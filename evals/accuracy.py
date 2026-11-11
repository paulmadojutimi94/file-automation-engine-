"""Accuracy and safety on generated messy folders, scored against the answer key.

Seeds 100-119 are held out: nothing in the code was tuned on them.
Run: PYTHONPATH=src python3 evals/accuracy.py
"""

from __future__ import annotations

import itertools
import json
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import content_multiset, manifest, save

from file_engine.engine import plan_directory
from file_engine.executor import apply_plan
from file_engine.generator import generate
from file_engine.planner import QUARANTINE_DIR
from file_engine.rules import Rules
from file_engine.undo import undo

SEEDS = range(100, 120)
FILES_PER_TREE = 2000


def pairs(groups) -> set[frozenset]:
    return {frozenset(p) for g in groups for p in itertools.combinations(sorted(g), 2)}


def run_seed(seed: int, workdir: Path) -> dict:
    root = workdir / f"tree-{seed}"
    key = generate(root, seed=seed, n_files=FILES_PER_TREE)
    rel = lambda p: str(p.relative_to(root))  # noqa: E731
    before_exact = manifest(root, with_mtime=True)
    dir_times = {d: d.stat().st_mtime_ns for d in root.rglob("*") if d.is_dir() and not d.is_symlink()}
    before = manifest(root)
    before_contents = content_multiset(root)

    # 1. dry run must change nothing at all, not even a timestamp
    plan = plan_directory(root, rename=True)
    dry_run_clean = (
        manifest(root, with_mtime=True) == before_exact
        and dir_times == {d: d.stat().st_mtime_ns for d in dir_times}  # not even a folder was touched
        and not (root / ".file-engine").exists()
    )

    # 2. duplicates vs answer key
    found = [[rel(r.path) for r in (g.keeper, *g.copies)] for g in plan.duplicates.groups]
    true_pairs, found_pairs = pairs(key["duplicate_groups"]), pairs(found)
    trap_files = {p for p, info in key["files"].items() if info.get("trap") in {"same_size", "same_edges", "same_head"}}
    grouped = {p for g in found for p in g}

    # 3. categories vs answer key (the generator's own ext -> folder table)
    rules = Rules()
    cat_right = sum(rules.category(Path(p).name) == info["category"] for p, info in key["files"].items())
    cat_misses = Counter(
        (Path(p).suffix or "(none)", info["category"], rules.category(Path(p).name))
        for p, info in key["files"].items()
        if rules.category(Path(p).name) != info["category"]
    )

    # 4. hidden files and symlinks left alone
    skipped = {rel(s.path) for s in plan.scan.skipped}
    skip_right = skipped == set(key["skipped"])

    # 5. apply: same bytes before and after, one live copy per duplicate group
    applied = apply_plan(plan)
    after_contents = content_multiset(root)
    live = Counter(v[1] for k, v in manifest(root).items() if v[0] == "file" and not k.startswith(QUARANTINE_DIR + "/"))
    one_live = sum(live[before[g[0]][1]] == 1 for g in key["duplicate_groups"])

    # 6. running again finds nothing to do
    rerun = plan_directory(root, rename=True)

    # 7. undo puts every byte and timestamp back
    undone = undo(applied.journal, apply=True)
    restored = manifest(root, with_mtime=True) == before_exact

    shutil.rmtree(root)
    root.with_name(root.name + ".answers.json").unlink()
    return {
        "seed": seed,
        "files": len(key["files"]),
        "bytes": key["stats"]["bytes"],
        "true_pairs": len(true_pairs),
        "found_pairs": len(found_pairs),
        "correct_pairs": len(true_pairs & found_pairs),
        "true_groups": len(key["duplicate_groups"]),
        "exact_groups": len({frozenset(g) for g in found} & {frozenset(g) for g in key["duplicate_groups"]}),
        "traps": len(trap_files),
        "traps_grouped": len(trap_files & grouped),
        "empty_grouped": sum(1 for p in grouped if key["files"][p]["size"] == 0),
        "category_right": cat_right,
        "category_misses": {f"{a} expected {b} got {c}": n for (a, b, c), n in cat_misses.items()},
        "skips_right": skip_right,
        "dry_run_clean": dry_run_clean,
        "apply_counts": dict(applied.counts),
        "bytes_preserved": after_contents == before_contents,
        "groups_with_one_live_copy": one_live,
        "rerun_actions": len(rerun.actions),
        "undo_steps": dict(Counter(s.status for s in undone.steps)),
        "undo_exact": restored,
        "collisions_renamed": sum(1 for a in plan.moves if a.dst.stem.rsplit("-", 1)[-1].isdigit()),
    }


def main() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        rows = [run_seed(s, Path(tmp)) for s in SEEDS]
    tot = lambda k: sum(r[k] for r in rows)  # noqa: E731
    files = tot("files")
    result = {
        "seeds": f"{SEEDS.start}-{SEEDS.stop - 1}",
        "trees": len(rows),
        "files": files,
        "bytes": tot("bytes"),
        "dup_pair_precision": tot("correct_pairs") / tot("found_pairs"),
        "dup_pair_recall": tot("correct_pairs") / tot("true_pairs"),
        "dup_groups_exact": f"{tot('exact_groups')}/{tot('true_groups')}",
        "traps_grouped": f"{tot('traps_grouped')}/{tot('traps')}",
        "empty_files_grouped": tot("empty_grouped"),
        "category_accuracy": tot("category_right") / files,
        "category_misses": dict(sum((Counter(r["category_misses"]) for r in rows), Counter())),
        "skips_right": f"{sum(r['skips_right'] for r in rows)}/{len(rows)}",
        "dry_run_clean": f"{sum(r['dry_run_clean'] for r in rows)}/{len(rows)}",
        "bytes_preserved": f"{sum(r['bytes_preserved'] for r in rows)}/{len(rows)}",
        "moved": sum(r["apply_counts"].get("moved", 0) for r in rows),
        "not_moved": sum(v for r in rows for k, v in r["apply_counts"].items() if k != "moved"),
        "groups_with_one_live_copy": f"{tot('groups_with_one_live_copy')}/{tot('true_groups')}",
        "idempotent": f"{sum(r['rerun_actions'] == 0 for r in rows)}/{len(rows)}",
        "undo_exact": f"{sum(r['undo_exact'] for r in rows)}/{len(rows)}",
        "per_seed": rows,
    }
    save("accuracy", result)
    return result


if __name__ == "__main__":
    out = main()
    print(json.dumps({k: v for k, v in out.items() if k != "per_seed"}, indent=2, ensure_ascii=False))
