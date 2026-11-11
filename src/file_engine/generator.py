"""Build a realistic messy folder, plus an answer key saying what's really in it.

Every accuracy number in the README is measured against this key, so the
generator plants the traps that fool naive tools:
  * same size, different content        (fools size-only dedupe)
  * same first and last 8 KiB, different middle (fools edge-only dedupe)
  * empty files                         (all "identical", none worth removing)
  * 'Report.pdf' vs 'report.pdf', 'café' vs 'cafe' + accent  (name collisions)
  * .tar.gz, .JPG, no extension, hidden files, symlinks, a symlink loop
"""

from __future__ import annotations

import json
import os
import random
import unicodedata
from pathlib import Path

# The generator's OWN idea of where each extension belongs, written separately
# from rules.py so the two can disagree (and the eval would notice).
EXT_CATEGORY = {
    ".jpg": "Images",
    ".JPG": "Images",
    ".jpeg": "Images",
    ".png": "Images",
    ".HEIC": "Images",
    ".gif": "Images",
    ".pdf": "Documents",
    ".docx": "Documents",
    ".txt": "Documents",
    ".md": "Documents",
    ".PDF": "Documents",
    ".xlsx": "Spreadsheets",
    ".csv": "Spreadsheets",
    ".pptx": "Presentations",
    ".key": "Presentations",
    ".mp3": "Audio",
    ".m4a": "Audio",
    ".wav": "Audio",
    ".mp4": "Video",
    ".mov": "Video",
    ".MOV": "Video",
    ".zip": "Archives",
    ".tar.gz": "Archives",
    ".TAR.GZ": "Archives",
    ".7z": "Archives",
    ".py": "Code",
    ".json": "Code",
    ".html": "Code",
    ".dmg": "Installers",
    ".exe": "Installers",
    ".ttf": "Fonts",
    "": "Other",
    ".bak": "Other",
    ".tmp": "Other",
    ".xyz": "Other",
}
FOLDERS = [
    "Downloads",
    "Downloads/Telegram Desktop",
    "Desktop",
    "Desktop/old stuff",
    "Documents/misc",
    "Documents/2024 taxes",
    "Pictures/phone dump",
    "Pictures/phone dump/2023",
    "tmp",
    "projects/website backup",
    "Music/unsorted",
    "Work/Q3 Planning",
    "Work/Q3 Planning/drafts",
]
STEMS = [
    "invoice {n}",
    "IMG_{n:04d}",
    "Screenshot 2026-03-1{d} at 10.{n2:02d}.{d}{d}",
    "report final",
    "Report FINAL v{d}",
    "notes",
    "budget {y}",
    "holiday photo {n}",
    "scan_{n}",
    "Meeting Recording {n}",
    "résumé",
    "menu du café",
    "presentation draft",
    "setup",
    "backup {y}",
    "song {n}",
    "data export ({d})",
    "Kids' party 🎉 {n}",
    "untitled",
    "Document{n}",
    "WhatsApp Image {y}-0{d}-1{d} at 09.{n2:02d}",
    "thesis_v{d}_FINAL_final",
    "photo  with   spaces {n}",
    "tax return {y}",
    "contract [signed] {n}",
]
COPY_NAMES = ["{stem} (1)", "{stem} (2)", "Copy of {stem}", "{stem} copy", "{stem}", "{stem} - Copy"]
EDGE = 8 * 1024


def _size(rng: random.Random) -> int:
    r = rng.random()
    if r < 0.55:
        return rng.randint(200, 16 * 1024)  # small: fits inside the edge window
    if r < 0.93:
        return rng.randint(16 * 1024 + 1, 160 * 1024)
    return rng.randint(160 * 1024, 2 * 1024 * 1024)


class _Tree:
    def __init__(self, out: Path, rng: random.Random):
        self.out, self.rng = out, rng
        self.files: dict[str, dict] = {}  # rel path -> {content, category, size, trap?}
        self.skipped: dict[str, str] = {}
        self.taken: set[str] = set()  # case/accent-folded names, so the tree also works on a Mac
        self.contents: list[bytes] = []
        self.bytes = 0

    def _free(self, folder: str, stem: str, ext: str) -> str:
        n = 1
        while True:
            name = f"{stem}{ext}" if n == 1 else f"{stem} ({n}){ext}"
            rel = f"{folder}/{name}"
            key = unicodedata.normalize("NFC", rel).casefold()
            if key not in self.taken:
                self.taken.add(key)
                return rel
            n += 1

    def add(self, folder: str, stem: str, ext: str, content_id: int, trap: str | None = None) -> str:
        rel = self._free(folder, stem, ext)
        data = self.contents[content_id]
        path = self.out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        # spread modified times over ~3 years so {date} renames vary
        mtime = 1_700_000_000 + self.rng.randint(0, 90_000_000)
        os.utime(path, (mtime, mtime))
        entry = {"content": content_id, "category": EXT_CATEGORY[ext], "size": len(data)}
        if trap:
            entry["trap"] = trap
        self.files[rel] = entry
        self.bytes += len(data)
        return rel

    def new_content(self, data: bytes) -> int:
        self.contents.append(data)
        return len(self.contents) - 1

    def stem(self) -> str:
        r = self.rng
        return r.choice(STEMS).format(
            n=r.randint(1, 9999), n2=r.randint(0, 59), d=r.randint(1, 9), y=r.randint(2019, 2026)
        )


def generate(out: Path, *, seed: int = 7, n_files: int = 1000, dup_share: float = 0.18) -> dict:
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"{out} is not empty; refusing to mix generated files with real ones")
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    t = _Tree(out, rng)
    exts = list(EXT_CATEGORY)

    n_traps = max(8, n_files // 50)
    n_unique = int(n_files * (1 - dup_share)) - n_traps
    originals: list[tuple[str, str, int]] = []  # (stem, ext, content_id)
    for _ in range(n_unique):
        stem, ext = t.stem(), rng.choice(exts)
        cid = t.new_content(rng.randbytes(_size(rng)))
        t.add(rng.choice(FOLDERS), stem, ext, cid)
        originals.append((stem, ext, cid))

    # planted duplicates: same bytes, new name and/or folder
    while len(t.files) < n_files - n_traps:
        stem, ext, cid = rng.choice(originals)
        new_stem = rng.choice(COPY_NAMES).format(stem=stem)
        if rng.random() < 0.15:
            new_stem = t.stem()  # a copy with a completely different name
        t.add(rng.choice(FOLDERS), new_stem, ext, cid)

    # traps: near-duplicates that must NOT be grouped
    big = [o for o in originals if len(t.contents[o[2]]) > 2 * EDGE + 64]
    # without replacement: two traps made from one original by the same edit
    # would be byte-identical, i.e. real duplicates the key calls different
    for i, (stem, ext, cid) in enumerate(rng.sample(big, n_traps)):
        data = bytearray(t.contents[cid])
        kind = ("same_size", "same_edges", "same_head")[i % 3]
        if kind == "same_size":
            data = bytearray(rng.randbytes(len(data)))
        elif kind == "same_edges":
            mid = len(data) // 2
            data[mid] ^= 0xFF  # one byte in the middle: edge digest identical
        else:
            data[-1] ^= 0xFF  # last byte only
        t.add(rng.choice(FOLDERS), f"{stem} {kind.replace('_', ' ')}", ext, t.new_content(bytes(data)), trap=kind)

    # edge cases
    empty = t.new_content(b"")
    for i in range(6):
        t.add(rng.choice(FOLDERS), f"empty {i}", rng.choice([".txt", ".md", ""]), empty, trap="empty")
    t.add("Desktop", "Report", ".pdf", t.new_content(rng.randbytes(3000)), trap="case_collision")
    t.add("Downloads", "report", ".PDF", t.new_content(rng.randbytes(3001)), trap="case_collision")
    t.add(
        "Documents/misc", unicodedata.normalize("NFC", "café"), ".txt", t.new_content(b"nfc"), trap="unicode_collision"
    )
    t.add("Downloads", unicodedata.normalize("NFD", "café"), ".txt", t.new_content(b"nfd!"), trap="unicode_collision")
    t.add("Downloads", "backup", ".tar.gz", t.new_content(rng.randbytes(5000)))
    t.add("Desktop", "Photos 2024", ".TAR.GZ", t.new_content(rng.randbytes(5001)))
    t.add("Desktop", "README", "", t.new_content(b"# notes\n"))
    t.add("Downloads", "x" * 240, ".pdf", t.new_content(rng.randbytes(999)), trap="long_name")

    for rel in (".env", "Pictures/.hidden photo.jpg"):
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_bytes(b"secret=1\n")
        t.skipped[rel] = "hidden"
    some_file = next(iter(t.files))
    (out / "Desktop/shortcut.pdf").symlink_to(out / some_file)
    (out / "Desktop/loop").symlink_to(out / "Desktop", target_is_directory=True)
    t.skipped["Desktop/shortcut.pdf"] = "symlink"
    t.skipped["Desktop/loop"] = "symlink"

    by_content: dict[int, list[str]] = {}
    for rel, info in t.files.items():
        if info["size"] > 0:
            by_content.setdefault(info["content"], []).append(rel)
    groups = sorted(sorted(v) for v in by_content.values() if len(v) > 1)

    key_path = out.parent / f"{out.name}.answers.json"
    key = {
        "seed": seed,
        "files": t.files,
        "skipped": t.skipped,
        "duplicate_groups": groups,
        "stats": {
            "files": len(t.files),
            "bytes": t.bytes,
            "duplicate_groups": len(groups),
            "duplicate_copies": sum(len(g) - 1 for g in groups),
            "answer_key": str(key_path),
        },
    }
    key_path.write_text(json.dumps(key, indent=1, ensure_ascii=False), encoding="utf-8")
    return key
