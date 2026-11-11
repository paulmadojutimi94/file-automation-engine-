import csv
import json

from file_engine import safety
from file_engine.cli import main
from file_engine.generator import generate


def test_safety_display_escapes_controls_and_bidi():
    assert safety.display("a\x1b[2Jb\nc\u202ed") == "a\\x1b[2Jb\\x0ac\\u202ed"


def test_md_cell_cannot_break_a_table():
    cell = safety.md_cell("x | y\n<script>")
    assert "|" not in cell.replace("\\|", "") and "<" not in cell and "\n" not in cell


def test_csv_cell_neutralises_formulas():
    assert safety.csv_cell("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert safety.csv_cell("normal.txt") == "normal.txt"


def test_cli_end_to_end(tmp_path, make, capsys):
    root, reports = tmp_path / "messy", tmp_path / "reports"
    make(root / "a.jpg", b"1")
    make(root / "b" / "a (1).jpg", b"1")
    make(root / "=cmd|calc.csv", b"2")

    assert main(["organize", str(root), "--report-dir", str(reports), "--rename"]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and (root / "a.jpg").exists()

    assert main(["organize", str(root), "--report-dir", str(reports), "--rename", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "3 moved" in out
    summary = json.loads((reports / "report.json").read_text())
    assert summary["mode"] == "applied" and summary["duplicates"]["copies"] == 1
    rows = list(csv.reader((reports / "actions.csv").open()))
    assert all(not cell.startswith("=") for row in rows for cell in row)

    journal = summary["applied"]["journal"]
    assert main(["undo", journal, "--apply"]) == 0
    assert (root / "a.jpg").exists() and (root / "=cmd|calc.csv").exists()


def test_cli_scan_and_errors(tmp_path, make, capsys):
    make(tmp_path / "f.txt")
    assert main(["scan", str(tmp_path), "--report-dir", str(tmp_path / "r")]) == 0
    assert "Nothing was changed" in capsys.readouterr().out
    assert main(["organize", str(tmp_path / "missing"), "--report-dir", str(tmp_path / "r")]) == 2
    assert main(["organize", str(tmp_path), "--template", "../{name}"]) == 2


def test_generator_answer_key_is_consistent(tmp_path):
    key = generate(tmp_path / "g", seed=3, n_files=200)
    assert key["stats"]["files"] == len(key["files"]) >= 200
    for group in key["duplicate_groups"]:
        contents = {(tmp_path / "g" / rel).read_bytes() for rel in group}
        assert len(contents) == 1
