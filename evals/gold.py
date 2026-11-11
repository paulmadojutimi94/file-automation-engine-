"""Categorisation on 40 hand-labelled names the generator never makes.
Run: PYTHONPATH=src python3 evals/gold.py"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import save

from file_engine.rules import Rules

# Recorded by hand before any rule was changed for these names. After the fix
# the set has been "seen", so 40/40 is a regression test, not an accuracy claim.
FIRST_RUN = "32/40"


def main() -> dict:
    gold = json.loads((Path(__file__).parent / "gold_names.json").read_text(encoding="utf-8"))["names"]
    rules = Rules()
    misses = {n: {"expected": want, "got": rules.category(n)} for n, want in gold.items() if rules.category(n) != want}
    result = {"total": len(gold), "right": len(gold) - len(misses), "first_run": FIRST_RUN, "misses": misses}
    save("gold", result)
    return result


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
