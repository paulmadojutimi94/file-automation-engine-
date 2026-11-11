"""Turn messy file names into clean, predictable ones."""

from __future__ import annotations

import re
import string
import unicodedata
from datetime import datetime

from . import safety
from .models import FileRecord
from .rules import Rules

DEFAULT_TEMPLATE = "{date}_{name}"
TEMPLATE_FIELDS = frozenset({"date", "name", "category"})
MAX_NAME_BYTES = 255  # the limit on Linux (ext4), macOS (APFS) and Windows (NTFS, roughly)

_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}_")
_RUNS = re.compile(r"[-_]*-[-_]*")  # any run of - and _ containing a '-' becomes one '-'


def slugify(stem: str) -> str:
    """'Report FINAL (v2)!!' -> 'report-final-v2'. Keeps letters and digits in
    any script (é, 日本), '_' and '-'; everything else becomes '-'."""
    stem = unicodedata.normalize("NFC", stem).lower()
    chars = [ch if (ch.isalnum() or ch in "_-") else "-" for ch in stem]
    slug = _RUNS.sub("-", "".join(chars)).strip("-_")
    return slug or "file"


def validate_template(template: str) -> None:
    """Allow only {date}, {name}, {category}: no attribute access, no '/'."""
    for _literal, field, spec, conv in string.Formatter().parse(template):
        if field is None:
            continue
        if field not in TEMPLATE_FIELDS or spec or conv:
            raise ValueError(f"template field {{{field}}} not allowed; use {sorted(TEMPLATE_FIELDS)}")
    literal = template.replace("{", "").replace("}", "")
    if not safety.is_safe_component(literal or "x") or literal.startswith("."):
        raise ValueError("template must be a single, visible file name (no '/', '..' or leading '.')")


def fit_name(stem: str, ext: str, suffix: str = "") -> str:
    """stem + suffix + ext, cutting the stem so the whole name fits in 255 bytes
    of UTF-8 without splitting a multi-byte character."""
    budget = MAX_NAME_BYTES - len(suffix.encode()) - len(ext.encode())
    raw = stem.encode("utf-8")
    if len(raw) > budget:
        stem = raw[:budget].decode("utf-8", errors="ignore").rstrip("-_ ")
    return f"{stem}{suffix}{ext}"


def split_name(name: str, rules: Rules) -> tuple[str, str]:
    """('Holiday.Photo', '.jpg') for 'Holiday.Photo.JPG'. ext keeps the original case."""
    ext_len = len(rules.extension(name))
    return (name[:-ext_len], name[-ext_len:]) if ext_len else (name, "")


def clean_name(record: FileRecord, rules: Rules, template: str, category: str) -> tuple[str, str]:
    """Return (stem, ext) of the new name. ext is lower-cased."""
    stem, ext = split_name(record.path.name, rules)
    slug = slugify(stem)
    if "{date}" in template:
        slug = _DATE_PREFIX.sub("", slug) or "file"  # don't stack dates on re-runs
    date = datetime.fromtimestamp(record.mtime).strftime("%Y-%m-%d")
    new_stem = template.format(date=date, name=slug, category=category.lower())
    return new_stem, ext.lower()
