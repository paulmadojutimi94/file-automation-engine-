import json
import os
import unicodedata

import pytest

from file_engine import executor
from file_engine.engine import plan_directory
from file_engine.executor import apply_plan, move_no_clobber
from file_engine.models import ActionKind
from file_engine.undo import undo


def test_dry_run_changes_nothing(tmp_path, make, snapshot):
    make(tmp_path / "a.jpg", b"1")
    make(tmp_path / "b" / "c.pdf", b"2")
    before = snapshot(tmp_path)
    plan = plan_directory(tmp_path, rename=True)
    assert len(plan.actions) == 2
    assert snapshot(tmp_path) == before
    assert not (tmp_path / ".file-engine").exists()


def test_apply_organises_and_quarantines(tmp_path, make, snapshot):
    make(tmp_path / "Photo.JPG", b"pic")
    make(tmp_path / "dl" / "Photo (1).JPG", b"pic")
    make(tmp_path / "notes.txt", b"n")
    before = snapshot(tmp_path)
    result = apply_plan(plan_directory(tmp_path))
    assert result.counts == {"moved": 3}
    after = snapshot(tmp_path)
    assert set(after) == {"Images/Photo.JPG", "Documents/notes.txt", "_duplicates/dl/Photo (1).JPG"}
    assert sorted(after.values()) == sorted(before.values())  # same bytes, nothing lost


def test_rerun_is_a_no_op(tmp_path, make):
    make(tmp_path / "Report FINAL.pdf", b"r")
    make(tmp_path / "x" / "report final.pdf", b"different")
    apply_plan(plan_directory(tmp_path, rename=True))
    again = plan_directory(tmp_path, rename=True)
    assert again.actions == [] and again.unchanged == 2


def test_collisions_get_numbered_including_case_and_accents(tmp_path, make):
    make(tmp_path / "a" / "Report.pdf", b"1")
    make(tmp_path / "b" / "report.pdf", b"2")
    make(tmp_path / "c" / unicodedata.normalize("NFC", "café.txt"), b"3")
    make(tmp_path / "d" / unicodedata.normalize("NFD", "café.txt"), b"4")
    plan = plan_directory(tmp_path, rename=True, template="{name}")
    names = sorted(a.dst.name for a in plan.actions)
    assert names == ["café-2.txt", "café.txt", "report-2.pdf", "report.pdf"]


def test_file_already_in_place_keeps_its_name(tmp_path, make):
    make(tmp_path / "Documents" / "report.pdf", b"stays")
    make(tmp_path / "Report.PDF", b"incoming")
    plan = plan_directory(tmp_path, rename=True, template="{name}")
    assert plan.unchanged == 1
    assert [a.dst.name for a in plan.actions] == ["report-2.pdf"]


def test_quarantine_runs_before_keeper_moves(tmp_path, make):
    make(tmp_path / "a.txt", b"same")
    make(tmp_path / "z" / "a copy.txt", b"same")
    plan = plan_directory(tmp_path)
    assert [a.kind for a in plan.actions] == [ActionKind.QUARANTINE, ActionKind.ORGANIZE]
    assert apply_plan(plan).counts == {"moved": 2}


def test_dest_outside_root(tmp_path, make):
    make(tmp_path / "in" / "a.mp3", b"m")
    plan = plan_directory(tmp_path / "in", dest=tmp_path / "out")
    apply_plan(plan)
    assert (tmp_path / "out" / "Audio" / "a.mp3").read_bytes() == b"m"


def test_stale_plan_is_skipped(tmp_path, make):
    f = make(tmp_path / "a.txt", b"v1")
    plan = plan_directory(tmp_path)
    make(f, b"v2 longer", mtime=1_760_000_000)
    result = apply_plan(plan)
    assert result.counts == {"skipped": 1} and f.read_bytes() == b"v2 longer"


def test_changed_duplicate_is_not_quarantined(tmp_path, make):
    make(tmp_path / "a.txt", b"same")
    copy = make(tmp_path / "b" / "a copy.txt", b"same")
    plan = plan_directory(tmp_path)
    copy.write_bytes(b"diff")  # same size, new content
    os.utime(copy, (1_750_000_000, 1_750_000_000))  # and same mtime: only the hash can tell
    outcome = apply_plan(plan).outcomes[0]
    assert outcome.status == "skipped" and "no longer a duplicate" in outcome.detail
    assert copy.exists()


def test_move_never_overwrites(tmp_path, make):
    src, dst = make(tmp_path / "src", b"new"), make(tmp_path / "dst", b"old")
    with pytest.raises(FileExistsError):
        move_no_clobber(src, dst)
    assert dst.read_bytes() == b"old" and src.exists()


def test_cross_device_fallback_copies_without_clobbering(tmp_path, make, monkeypatch):
    def no_links(*a, **k):
        raise OSError(18, "Invalid cross-device link")  # EXDEV

    monkeypatch.setattr(executor.os, "link", no_links)
    src = make(tmp_path / "src", b"data", mtime=1_700_000_000)
    move_no_clobber(src, tmp_path / "dst")
    assert not src.exists() and (tmp_path / "dst").read_bytes() == b"data"
    assert (tmp_path / "dst").stat().st_mtime == 1_700_000_000
    other = make(tmp_path / "src2", b"x")
    with pytest.raises(FileExistsError):
        move_no_clobber(other, tmp_path / "dst")


def test_undo_restores_exactly(tmp_path, make, snapshot):
    make(tmp_path / "Holiday.JPG", b"1")
    make(tmp_path / "x" / "Holiday (1).JPG", b"1")
    make(tmp_path / "x" / "y" / "Budget.xlsx", b"2")
    before = snapshot(tmp_path)
    result = apply_plan(plan_directory(tmp_path, rename=True))
    assert snapshot(tmp_path) != before
    dry = undo(result.journal)
    assert {s.status for s in dry.steps} == {"would_restore"} and snapshot(tmp_path) != before
    done = undo(result.journal, apply=True)
    assert {s.status for s in done.steps} == {"restored"}
    assert snapshot(tmp_path) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == [".file-engine", "Holiday.JPG", "x"]
    with pytest.raises(ValueError, match="already undone"):
        undo(result.journal, apply=True)


def test_undo_refuses_tampered_journal(tmp_path, make):
    root = tmp_path / "root"
    make(root / "a.txt", b"a")
    result = apply_plan(plan_directory(root))
    lines = result.journal.read_text().splitlines()
    move = json.loads(lines[-1])
    move["src"] = str(tmp_path / "elsewhere" / "planted.txt")
    lines[-1] = json.dumps(move)
    result.journal.write_text("\n".join(lines) + "\n")
    steps = undo(result.journal, apply=True).steps
    assert steps[0].status == "refused"
    assert not (tmp_path / "elsewhere").exists()


def test_symlinked_category_folder_is_refused(tmp_path, make):
    root, outside = tmp_path / "root", tmp_path / "outside"
    outside.mkdir()
    make(root / "a.pdf", b"a")
    (root / "Documents").symlink_to(outside, target_is_directory=True)
    plan = plan_directory(root)
    assert plan.actions == [] and len(plan.refused) == 1
    assert list(outside.iterdir()) == []
