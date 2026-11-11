import json
import unicodedata

import pytest

from file_engine import naming
from file_engine.models import FileRecord
from file_engine.rules import Rules


@pytest.mark.parametrize(
    "name, category",
    [
        ("IMG_001.JPG", "Images"),
        ("backup.tar.gz", "Archives"),
        ("Photos.TAR.GZ", "Archives"),
        ("data.gz", "Archives"),
        ("report.v2.final.pdf", "Documents"),
        ("README", "Other"),
        ("weird.", "Other"),
        ("song.M4A", "Audio"),
        ("notes.txt.zip", "Archives"),
    ],
)
def test_category(name, category):
    assert Rules().category(name) == category


def test_custom_rules_from_json(tmp_path):
    f = tmp_path / "rules.json"
    f.write_text(json.dumps({"Receipts": [".pdf"], "Pics": ["jpg"]}))
    rules = Rules.from_json(f)
    assert rules.category("a.PDF") == "Receipts" and rules.category("b.jpg") == "Pics"
    assert rules.category("c.png") == "Other"


@pytest.mark.parametrize(
    "bad", [{"../escape": [".pdf"]}, {".hidden": [".pdf"]}, {"A": [".x"], "B": [".x"]}, ["not a dict"]]
)
def test_bad_rules_are_rejected(tmp_path, bad):
    f = tmp_path / "rules.json"
    f.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        Rules.from_json(f)


@pytest.mark.parametrize(
    "stem, slug",
    [
        ("Report FINAL (v2)!!", "report-final-v2"),
        ("photo  with   spaces", "photo-with-spaces"),
        ("thesis_v4_FINAL_final", "thesis_v4_final_final"),
        ("Kids' party 🎉 12", "kids-party-12"),
        ("résumé", "résumé"),
        ("日本語のファイル", "日本語のファイル"),
        ("!!!", "file"),
        ("a - b", "a-b"),
    ],
)
def test_slugify(stem, slug):
    assert naming.slugify(stem) == slug


def test_slugify_normalises_accents_to_one_form():
    nfd = unicodedata.normalize("NFD", "café")
    assert naming.slugify(nfd) == unicodedata.normalize("NFC", "café")


def test_clean_name_is_idempotent(tmp_path):
    rules = Rules()
    rec = FileRecord(tmp_path / "Holiday Photo.JPG", 1, 1_750_000_000)
    stem, ext = naming.clean_name(rec, rules, naming.DEFAULT_TEMPLATE, "Images")
    again = FileRecord(tmp_path / f"{stem}{ext}", 1, 1_750_000_000)
    assert naming.clean_name(again, rules, naming.DEFAULT_TEMPLATE, "Images") == (stem, ext)
    assert ext == ".jpg" and stem.endswith("_holiday-photo")


@pytest.mark.parametrize(
    "template", ["../{name}", "{name}/x", ".{name}", "{name.__class__}", "{other}", "{name!r}", "{date:%Y}"]
)
def test_dangerous_templates_rejected(template):
    with pytest.raises(ValueError):
        naming.validate_template(template)


def test_fit_name_respects_255_bytes_and_utf8():
    name = naming.fit_name("é" * 300, ".pdf", "-12")
    assert len(name.encode()) <= 255 and name.endswith("-12.pdf")
    name.encode().decode()  # no broken multi-byte character
