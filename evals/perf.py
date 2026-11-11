"""Latency, bytes read, and memory. Run: PYTHONPATH=src python3 evals/perf.py

Files are in the OS page cache after the first pass (warm). Numbers on a cold
spinning disk would be slower; the bytes-read figures don't change.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import machine, pct, save

from file_engine import hashing
from file_engine.engine import plan_directory
from file_engine.executor import apply_plan
from file_engine.generator import generate
from file_engine.scanner import scan
from file_engine.undo import undo

N_FILES = 5000
SRC = str(Path(__file__).resolve().parent.parent / "src")


def timed(fn, runs: int) -> list[float]:
    out = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t0) * 1000)
    return out


def summary(ms: list[float]) -> dict:
    return {"p50_ms": round(pct(ms, 50), 1), "p95_ms": round(pct(ms, 95), 1), "runs": len(ms)}


def naive_dedupe(root: Path) -> int:
    """What most scripts do: SHA-256 every file. Returns bytes read."""
    total = 0
    for p in root.rglob("*"):
        if p.is_file() and not p.is_symlink() and not p.name.startswith("."):
            total += hashing.full_digest(p)[1]
    return total


def main() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "tree"
        key = generate(root, seed=200, n_files=N_FILES)
        plan_directory(root, rename=True)  # warm the cache

        plan_ms = timed(lambda: plan_directory(root, rename=True), 20)
        plan = plan_directory(root, rename=True)
        files = scan(root).files
        sieve_ms = timed(lambda: hashing.find_duplicates(files), 10)
        naive_bytes = naive_dedupe(root)
        naive_ms = timed(lambda: naive_dedupe(root), 5)

        tracemalloc.start()
        plan_directory(root, rename=True)
        peak_mb = tracemalloc.get_traced_memory()[1] / 1e6
        tracemalloc.stop()

        apply_ms, undo_ms = [], []
        for _ in range(5):
            p = plan_directory(root, rename=True)
            t0 = time.perf_counter()
            res = apply_plan(p)
            apply_ms.append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            undo(res.journal, apply=True)
            undo_ms.append((time.perf_counter() - t0) * 1000)

        env = {**os.environ, "PYTHONPATH": SRC}
        cmd = [
            sys.executable,
            "-m",
            "file_engine",
            "organize",
            str(root),
            "--rename",
            "--report-dir",
            str(Path(tmp) / "reports"),
            "--show",
            "0",
        ]
        cli_ms = timed(lambda: subprocess.run(cmd, env=env, check=True, capture_output=True), 10)

        big = Path(tmp) / "big.bin"
        big.write_bytes(os.urandom(256 * 1024 * 1024))
        hashing.full_digest(big)
        hash_ms = min(timed(lambda: hashing.full_digest(big), 3))

        d = plan.duplicates
        result = {
            "machine": machine(),
            "files": len(plan.scan.files),
            "bytes": key["stats"]["bytes"],
            "plan_dry_run": summary(plan_ms),
            "files_per_second_planned": round(len(plan.scan.files) / (pct(plan_ms, 50) / 1000)),
            "dedupe_three_sieves": summary(sieve_ms),
            "naive_full_hash": summary(naive_ms),
            "bytes_read_tiered": d.bytes_read,
            "bytes_read_naive": naive_bytes,
            "bytes_saved_share": 1 - d.bytes_read / naive_bytes,
            "files_edge_hashed": d.files_edge_hashed,
            "files_full_hashed": d.files_full_hashed,
            "apply": summary(apply_ms),
            "undo": summary(undo_ms),
            "cli_cold_dry_run": summary(cli_ms),
            "peak_python_heap_mb": round(peak_mb, 2),
            "sha256_mb_per_s": round(256 / (hash_ms / 1000)),
        }
    save("perf", result)
    return result


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
