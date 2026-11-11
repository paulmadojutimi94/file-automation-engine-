"""Hostile file names and race conditions, with every defence on and then with
each defence switched off (ablation), to prove each one is doing real work.

An attack "succeeds" if it causes real harm: a file written or moved outside
the folder, user data overwritten or hidden in quarantine, a symlink planted
inside the organised folders, a failed run, or hostile text reaching the
terminal / Markdown / CSV unescaped.

Run: PYTHONPATH=src python3 evals/attacks.py
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
from common import save

from file_engine import planner, undo
from file_engine.cli import main as cli
from file_engine.engine import plan_directory
from file_engine.executor import apply_plan
from file_engine.rules import Rules

T = 1_750_000_000


def put(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (T, T))
    return path


def escaped_files(case: Path, root: Path) -> list[Path]:
    """Regular files under case/ that are neither in root/ nor the attacker's own outside/ dir."""
    return [
        p
        for p in case.rglob("*")
        if p.is_file() and not p.is_relative_to(root) and not p.is_relative_to(case / "outside")
    ]


def only_in_quarantine(root: Path) -> bool:
    """True if some content now exists ONLY under _duplicates/ (data hidden as a 'duplicate')."""
    live, quarantined = set(), set()
    for p in root.rglob("*"):
        if p.is_file() and not p.is_symlink() and ".file-engine" not in p.parts:
            (quarantined if planner.QUARANTINE_DIR in p.parts else live).add(p.read_bytes())
    return bool(quarantined - live)


def run_cli(*args: str) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            cli(list(args))
        except Exception as exc:  # an attack that crashes the CLI also counts
            print(f"CRASH {exc!r}")
    return buf.getvalue()


# ----------------------------------------------------------------- attacks
# Each takes a fresh case/ folder containing root/ and outside/, returns True if harm was done.


def template_traversal(case: Path, root: Path) -> bool:
    put(root / "a.txt")
    (root / "Documents" / "x").mkdir(parents=True)  # folders already exist; see POSTMORTEM Incident 4
    with contextlib.suppress(ValueError):
        apply_plan(plan_directory(root, rename=True, template="x/../../../{name}"))
    return bool(escaped_files(case, root))


def rules_traversal(case: Path, root: Path) -> bool:
    put(root / "a.pdf")
    (root / "Docs").mkdir()
    rules_file = case / "outside" / "rules.json"
    rules_file.write_text(json.dumps({"Docs/../../escaped": [".pdf"]}))
    with contextlib.suppress(ValueError):
        apply_plan(plan_directory(root, rules=Rules.from_json(rules_file)))
    return bool(escaped_files(case, root))


def symlinked_category_folder(case: Path, root: Path) -> bool:
    put(root / "a.pdf")
    (root / "Documents").symlink_to(case / "outside", target_is_directory=True)
    apply_plan(plan_directory(root))
    return (case / "outside" / "a.pdf").exists()


def symlink_to_outside_file(case: Path, root: Path) -> bool:
    secret = put(case / "outside" / "secret.pdf", b"secret")
    (root / "shortcut.pdf").symlink_to(secret)
    apply_plan(plan_directory(root))
    reached = any(p.is_file() and not p.is_symlink() and p.read_bytes() == b"secret" for p in root.rglob("*"))
    return reached or not secret.exists() or secret.stat().st_nlink > 1


def symlink_loop(case: Path, root: Path) -> bool:
    put(root / "a.txt", b"only copy")
    put(root / "b.txt", b"another")
    (root / "loop").symlink_to(root, target_is_directory=True)
    apply_plan(plan_directory(root))
    return only_in_quarantine(root)


def duplicate_changed_after_plan(case: Path, root: Path) -> bool:
    put(root / "a.txt", b"same")
    copy = put(root / "z" / "a copy.txt", b"same")
    plan = plan_directory(root)
    put(copy, b"MINE")  # same size, same mtime, new content: now unique
    apply_plan(plan)
    return only_in_quarantine(root)


def symlink_swapped_in_after_plan(case: Path, root: Path) -> bool:
    secret = put(case / "outside" / "secret.pdf", b"secret")
    doc = put(root / "doc.pdf", b"doc")
    plan = plan_directory(root)
    doc.unlink()
    doc.symlink_to(secret)
    os.utime(doc, (T, T), follow_symlinks=False)
    apply_plan(plan)
    return any(p.is_symlink() for p in (root / "Documents").rglob("*")) if (root / "Documents").exists() else False


def destination_created_after_plan(case: Path, root: Path) -> bool:
    put(root / "a.pdf", b"incoming")
    plan = plan_directory(root)
    put(root / "Documents" / "a.pdf", b"user's important file")
    apply_plan(plan)
    return b"user's important file" not in {p.read_bytes() for p in root.rglob("*") if p.is_file()}


def case_and_accent_collision(case: Path, root: Path) -> bool:
    put(root / "a" / "Report.pdf", b"one")
    put(root / "b" / "report.pdf", b"two")
    put(root / "c" / "café.txt", b"nfc")
    put(root / "d" / "café.txt", b"nfd")
    apply_plan(plan_directory(root, rename=True, template="{name}"))
    contents = {p.read_bytes() for p in root.rglob("*") if p.is_file() and ".file-engine" not in p.parts}
    return not {b"one", b"two", b"nfc", b"nfd"} <= contents


def name_too_long(case: Path, root: Path) -> bool:
    put(root / ("x" * 250 + ".pdf"))
    return apply_plan(plan_directory(root, rename=True)).counts.get("moved", 0) != 1


def terminal_escape(case: Path, root: Path) -> bool:
    put(root / "\x1b]0;pwned\x07\x1b[2Jinvoice.pdf")
    out = run_cli("organize", str(root), "--report-dir", str(case / "reports"))
    return "\x1b" in out or "CRASH" in out


def bidi_override(case: Path, root: Path) -> bool:
    put(root / "invoice\u202efdp.exe")  # displays as "invoiceexe.pdf"
    out = run_cli("organize", str(root), "--report-dir", str(case / "reports"))
    md = (case / "reports" / "report.md").read_text()
    return "\u202e" in out or "\u202e" in md


def markdown_table_injection(case: Path, root: Path) -> bool:
    put(root / "a | b\n| injected | row |.txt")
    put(root / "<img src=x onerror=alert(1)>.txt")
    run_cli("organize", str(root), "--report-dir", str(case / "reports"))
    md = (case / "reports" / "report.md").read_text()
    return any(line.startswith("| injected") for line in md.splitlines()) or "<img" in md


def csv_formula(case: Path, root: Path) -> bool:
    put(root / '=HYPERLINK("http---evil","click").csv')
    put(root / "@SUM(1+1).txt")
    run_cli("organize", str(root), "--report-dir", str(case / "reports"))
    rows = list(csv.reader((case / "reports" / "actions.csv").open(encoding="utf-8")))
    return any(cell[:1] in ("=", "+", "-", "@") for row in rows[1:] for cell in row)


def _tampered_undo(case: Path, root: Path, field: str, value: Path) -> None:
    put(root / "a.txt", b"mine")
    result = apply_plan(plan_directory(root))
    lines = result.journal.read_text().splitlines()
    entry = json.loads(lines[-1])
    entry[field] = str(value)
    lines[-1] = json.dumps(entry)
    result.journal.write_text("\n".join(lines) + "\n")
    with contextlib.suppress(OSError):
        undo.undo(result.journal, apply=True)


def journal_restore_outside(case: Path, root: Path) -> bool:
    _tampered_undo(case, root, "src", case / "planted" / "a.txt")
    return bool(escaped_files(case, root))


def journal_pull_victim_in(case: Path, root: Path) -> bool:
    victim = put(case / "outside" / "victim.txt", b"victim")
    _tampered_undo(case, root, "dst", victim)
    return not victim.exists()


ATTACKS: dict[str, Callable[[Path, Path], bool]] = {
    f.__name__: f
    for f in (
        template_traversal,
        rules_traversal,
        symlinked_category_folder,
        symlink_to_outside_file,
        symlink_loop,
        duplicate_changed_after_plan,
        symlink_swapped_in_after_plan,
        destination_created_after_plan,
        case_and_accent_collision,
        name_too_long,
        terminal_escape,
        bidi_override,
        markdown_table_injection,
        csv_formula,
        journal_restore_outside,
        journal_pull_victim_in,
    )
}


# ----------------------------------------------------------------- ablations


def _naive_move(src: Path, dst: Path) -> None:
    os.replace(src, dst)  # what most scripts do: silently overwrites


ABLATIONS: list[tuple[str, dict[str, Callable], list[str]]] = [
    (
        "Path containment (one path segment, resolve() inside dest)",
        {
            "file_engine.safety.is_safe_component": lambda name: True,
            "file_engine.safety.ensure_inside": lambda path, root: None,
        },
        ["template_traversal", "rules_traversal", "symlinked_category_folder"],
    ),
    (
        "lstat(): never follow symlinks while scanning",
        {"file_engine.scanner.entry_stat": lambda p: p.stat()},
        ["symlink_to_outside_file", "symlink_loop"],
    ),
    (
        "lstat() AND stale-plan check together",
        {
            "file_engine.scanner.entry_stat": lambda p: p.stat(),
            "file_engine.executor.stale_reason": lambda action: None,
        },
        ["symlink_to_outside_file"],
    ),
    (
        "Re-hash copy and keeper before quarantine",
        {"file_engine.executor.verify_duplicate": lambda action: None},
        ["duplicate_changed_after_plan"],
    ),
    (
        "Stale-plan check (still a regular file, same size and mtime)",
        {"file_engine.executor.stale_reason": lambda action: None},
        ["symlink_swapped_in_after_plan"],
    ),
    (
        "No-clobber move (os.link, then unlink)",
        {"file_engine.executor.move_no_clobber": _naive_move},
        ["destination_created_after_plan"],
    ),
    (
        "Case- and accent-insensitive name claims",
        {"file_engine.planner.resolve_collision": lambda target, stem, ext, claimed: target},
        ["case_and_accent_collision"],
    ),
    (
        "Name claims AND no-clobber move together",
        {
            "file_engine.planner.resolve_collision": lambda target, stem, ext, claimed: target,
            "file_engine.executor.move_no_clobber": _naive_move,
        },
        ["case_and_accent_collision"],
    ),
    (
        "255-byte name limit",
        {"file_engine.naming.fit_name": lambda stem, ext, suffix="": f"{stem}{suffix}{ext}"},
        ["name_too_long"],
    ),
    (
        "Escaping in terminal, Markdown and CSV output",
        {
            "file_engine.safety.display": lambda s: s,
            "file_engine.safety.md_cell": lambda s: s,
            "file_engine.safety.csv_cell": lambda s: s,
        },
        ["terminal_escape", "bidi_override", "markdown_table_injection", "csv_formula"],
    ),
    (
        "Undo journal path checks",
        {"file_engine.undo.check_entry": lambda *a: None},
        ["journal_restore_outside", "journal_pull_victim_in"],
    ),
]


def run(name: str, patches: dict[str, Callable] | None = None) -> bool:
    with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
        for target, replacement in (patches or {}).items():
            stack.enter_context(mock.patch(target, replacement))
        case = Path(tmp)
        (case / "outside").mkdir()
        (case / "root").mkdir()
        try:
            return ATTACKS[name](case, case / "root")
        except Exception as exc:  # a crash is a successful denial of service
            print(f"  {name}: crashed with {exc!r}")
            return True


def main() -> dict:
    assert {a for _, _, attacks in ABLATIONS for a in attacks} == set(ATTACKS), "every attack needs an ablation"
    baseline = {name: run(name) for name in ATTACKS}
    ablation = []
    for label, patches, attacks in ABLATIONS:
        hits = [a for a in attacks if run(a, patches)]
        ablation.append({"defence_off": label, "succeeded": len(hits), "tried": len(attacks), "which": hits})
    result = {
        "attacks": len(ATTACKS),
        "succeeded_with_all_defences": sum(baseline.values()),
        "baseline": baseline,
        "ablation": ablation,
    }
    save("attacks", result)
    return result


if __name__ == "__main__":
    out = main()
    print(f"All defences on: {out['succeeded_with_all_defences']}/{out['attacks']} attacks succeeded")
    for row in out["ablation"]:
        print(f"  off: {row['defence_off']:<62} {row['succeeded']}/{row['tried']}  {row['which']}")
