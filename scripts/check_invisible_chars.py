"""Fail if code or docs contain invisible or right-to-left control characters.

Hostile file names are this tool's whole threat model, so our own source must
not hide any. Tests that need such characters write them as escapes (\\u202e).
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUFFIXES = {".py", ".md", ".toml", ".yml", ".json", ".txt"}
ALLOWED = {"\n", "\t", "‍", "️"}  # newline, tab, emoji joiners in docs


def bad_chars(text: str) -> list[tuple[int, str]]:
    out = []
    for line_no, line in enumerate(text.splitlines(), 1):
        for ch in line:
            if ch not in ALLOWED and unicodedata.category(ch) in {"Cc", "Cf", "Co", "Cn"}:
                out.append((line_no, f"U+{ord(ch):04X}"))
    return out


def main() -> int:
    problems = 0
    for path in sorted(ROOT.rglob("*")):
        if path.suffix not in SUFFIXES or any(p in path.parts for p in (".git", "demo", "reports")):
            continue
        for line_no, code in bad_chars(path.read_text(encoding="utf-8")):
            print(f"{path.relative_to(ROOT)}:{line_no}: invisible character {code}")
            problems += 1
    if problems == 0:
        print("no invisible characters")
    return 1 if problems else 0


if __name__ == "__main__":
    # self-test: the check must be able to fail, or it proves nothing
    assert bad_chars("ok\u202eevil") == [(1, "U+202E")], "checker is broken"
    raise SystemExit(main())
