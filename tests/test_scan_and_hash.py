import os

import pytest

from file_engine import hashing
from file_engine.hashing import EDGE, find_duplicates, keeper_key
from file_engine.models import FileRecord
from file_engine.scanner import scan


def test_scan_lists_regular_files_sorted(tmp_path, make):
    make(tmp_path / "b.txt")
    make(tmp_path / "sub" / "a.txt")
    result = scan(tmp_path)
    assert [f.path.name for f in result.files] == ["b.txt", "a.txt"]  # sorted by full path
    assert result.dirs_seen == 2


def test_scan_never_follows_symlinks(tmp_path, make):
    outside = make(tmp_path / "outside" / "secret.txt")
    root = tmp_path / "root"
    make(root / "real.txt")
    (root / "link.txt").symlink_to(outside)
    (root / "loop").symlink_to(root, target_is_directory=True)
    result = scan(root)
    assert [f.path.name for f in result.files] == ["real.txt"]
    assert {s.path.name: s.reason for s in result.skipped} == {"link.txt": "symlink", "loop": "symlink"}


def test_scan_skips_hidden_and_tool_files_unless_asked(tmp_path, make):
    make(tmp_path / ".env")
    make(tmp_path / ".git" / "config")
    make(tmp_path / "keep.txt")
    assert [f.path.name for f in scan(tmp_path).files] == ["keep.txt"]
    names = {f.path.name for f in scan(tmp_path, include_hidden=True).files}
    assert names == {".env", "keep.txt"}  # .git is never touched


def test_scan_exclude_is_silent(tmp_path, make):
    make(tmp_path / "_duplicates" / "x.txt")
    make(tmp_path / "y.txt")
    result = scan(tmp_path, exclude=[tmp_path / "_duplicates"])
    assert [f.path.name for f in result.files] == ["y.txt"] and result.skipped == []


def test_scan_rejects_a_file_as_root(tmp_path, make):
    with pytest.raises(NotADirectoryError):
        scan(make(tmp_path / "f.txt"))


def _records(paths):
    return [FileRecord(p, p.stat().st_size, p.stat().st_mtime) for p in paths]


def test_duplicates_found_and_traps_rejected(tmp_path, make):
    big = os.urandom(3 * EDGE)
    middle = bytearray(big)
    middle[len(big) // 2] ^= 1
    files = [
        make(tmp_path / "a.bin", big),
        make(tmp_path / "a copy.bin", big),
        make(tmp_path / "same-edges.bin", bytes(middle)),  # same first+last 8 KiB
        make(tmp_path / "same-size.bin", os.urandom(len(big))),
        make(tmp_path / "small1.txt", b"hello"),
        make(tmp_path / "small2.txt", b"hello"),
        make(tmp_path / "empty1", b""),
        make(tmp_path / "empty2", b""),
    ]
    result = find_duplicates(_records(files))
    groups = {frozenset(p.path.name for p in (g.keeper, *g.copies)) for g in result.groups}
    assert groups == {frozenset({"a.bin", "a copy.bin"}), frozenset({"small1.txt", "small2.txt"})}
    assert result.files_full_hashed == 3  # only the 3 that survived size + edges


def test_unique_sizes_cost_zero_reads(tmp_path, make):
    files = [make(tmp_path / f"{i}.bin", b"x" * (i + 1)) for i in range(20)]
    result = find_duplicates(_records(files))
    assert result.groups == [] and result.bytes_read == 0


def test_small_files_are_not_read_twice(tmp_path, make):
    files = [make(tmp_path / f"{i}.txt", b"same") for i in range(3)]
    result = find_duplicates(_records(files))
    assert result.files_full_hashed == 0 and result.bytes_read == 12


def test_empty_files_can_be_included(tmp_path, make):
    files = [make(tmp_path / "e1"), make(tmp_path / "e2")]
    for f in files:
        f.write_bytes(b"")
    assert len(find_duplicates(_records(files), min_size=0).groups) == 1


def test_edge_digest_of_small_file_is_full_sha256(tmp_path, make):
    f = make(tmp_path / "f", b"abc" * 100)
    assert hashing.edge_digest(f, 300)[0] == hashing.full_digest(f)[0]


def test_file_that_changed_size_is_reported_not_grouped(tmp_path, make):
    a, b = make(tmp_path / "a", b"x" * 10), make(tmp_path / "b", b"x" * 10)
    records = _records([a, b])
    b.write_bytes(b"x" * 11)  # grows after the scan
    result = find_duplicates(records)
    assert result.groups == [] and len(result.errors) == 1


@pytest.mark.parametrize(
    "names, keeper",
    [
        (["photo (1).jpg", "photo.jpg"], "photo.jpg"),
        (["Copy of report.pdf", "report.pdf"], "report.pdf"),
        (["notes copy.txt", "notes.txt"], "notes.txt"),
        (["b.txt", "a.txt"], "a.txt"),  # tie: alphabetical
    ],
)
def test_keeper_prefers_original_looking_names(tmp_path, names, keeper):
    recs = [FileRecord(tmp_path / n, 1, 100.0) for n in names]
    assert min(recs, key=keeper_key).path.name == keeper


def test_keeper_prefers_older_file_when_names_equal(tmp_path):
    old, new = FileRecord(tmp_path / "x" / "a.txt", 1, 1.0), FileRecord(tmp_path / "a.txt", 1, 2.0)
    assert min([new, old], key=keeper_key) is old
