"""Rebuild the 3-minute demo video from the project's REAL outputs.

  PYTHONPATH=src python3 docs/demo/make_video.py

Needs Pillow and ffmpeg. Runs `make demo`-equivalent steps in a temp folder,
reads evals/results/*.json, draws one 1280x720 frame per scene with a caption,
and stitches them into docs/demo/file-engine-demo.mp4.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "demo" / "file-engine-demo.mp4"
W, H = 1280, 720
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
SANS, BOLD, MONO = (str(FONT_DIR / f) for f in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSansMono.ttf"))
BG, INK, MUTED, ACCENT, TERM, TERM_INK, GOOD, BAD = (
    "#f7f5f0",
    "#1d232b",
    "#5d6773",
    "#2f6f6a",
    "#14181d",
    "#d9e1e8",
    "#7fc8a9",
    "#e58f7c",
)


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def clean(text: str) -> str:
    """DejaVu has no emoji; draw a plain marker instead of a missing-glyph box."""
    return "".join(ch if ord(ch) <= 0xFFFF else "*" for ch in text)


def run(*args: str, cwd: Path) -> str:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    out = subprocess.run(
        [sys.executable, "-m", "file_engine", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True
    ).stdout
    return out


# ------------------------------------------------------------------ drawing


def canvas(dark: bool = False) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), TERM if dark else BG)
    return img, ImageDraw.Draw(img)


def caption(d: ImageDraw.ImageDraw, text: str) -> None:
    d.rectangle([0, H - 64, W, H], fill="#000000")
    f = font(SANS, 24)
    w = d.textlength(text, font=f)
    d.text(((W - w) / 2, H - 48), text, font=f, fill="#ffffff")


def heading(d: ImageDraw.ImageDraw, text: str, dark: bool = False) -> None:
    d.text((60, 40), text, font=font(BOLD, 34), fill=TERM_INK if dark else INK)


def terminal(title: str, lines: list[str], cap: str, size: int = 17) -> Image.Image:
    img, d = canvas(dark=True)
    d.rectangle([0, 0, W, 36], fill="#262c33")
    for i, c in enumerate(("#e5695d", "#e8b04b", "#62c25b")):
        d.ellipse([16 + i * 22, 12, 28 + i * 22, 24], fill=c)
    d.text((100, 9), title, font=font(SANS, 16), fill="#9aa5b1")
    f = font(MONO, size)
    y = 52
    for line in lines:
        colour = TERM_INK
        if line.startswith("$"):
            colour = GOOD
        elif "DRY RUN" in line or line.startswith("Applied"):
            colour = "#f2d27a"
        d.text((24, y), clean(line)[:118], font=f, fill=colour)
        y += size + 6
        if y > H - 90:
            break
    caption(d, cap)
    return img


def slide(title: str, bullets: list[str], cap: str, size: int = 28) -> Image.Image:
    img, d = canvas()
    heading(d, title)
    y = 120
    for b in bullets:
        if b.startswith("  "):
            d.text((110, y), clean(b.strip()), font=font(SANS, size - 6), fill=MUTED)
            y += size + 10
        else:
            d.text((60, y), "•", font=font(BOLD, size), fill=ACCENT)
            d.text((95, y), clean(b), font=font(SANS, size), fill=INK)
            y += size + 22
    caption(d, cap)
    return img


def table(
    title: str, rows: list[tuple[str, str]], cap: str, col: int = 640, size: int = 22, highlight: bool = True
) -> Image.Image:
    img, d = canvas()
    heading(d, title)
    y = 112
    for i, (a, b) in enumerate(rows):
        if i % 2 == 0:
            d.rectangle([50, y - 6, W - 50, y + size + 8], fill="#ebe7de")
        d.text((66, y), clean(a), font=font(SANS, size), fill=INK)
        good = (highlight and b.startswith(("0 /", "100", "20/20", "0 (", "€0"))) or b.endswith("/ 6,432")
        d.text((col, y), clean(b), font=font(BOLD, size), fill=ACCENT if good else INK)
        y += size + 16
    caption(d, cap)
    return img


def title_card() -> Image.Image:
    img, d = canvas()
    d.text((80, 220), "file-automation-engine", font=font(BOLD, 64), fill=INK)
    d.text((84, 310), "Stop organising files by hand.", font=font(SANS, 34), fill=MUTED)
    d.text((84, 370), "Scan · dedupe · rename · organise · report", font=font(SANS, 28), fill=ACCENT)
    d.text((84, 420), "Dry run first. Never overwrite. Undo always.", font=font(SANS, 28), fill=ACCENT)
    d.text(
        (84, 560), "Python standard library only · 68 tests · every number measured", font=font(SANS, 20), fill=MUTED
    )
    caption(d, "A Python tool that tidies a messy folder safely, with numbers to prove it.")
    return img


def pipeline() -> Image.Image:
    img, d = canvas()
    heading(d, "How it works: plan first, act second")
    boxes = [
        ("scan", "lstat every entry,\nnever follow links"),
        ("3 sieves", "size → first+last\n8 KiB → SHA-256"),
        ("plan", "category, clean name,\nfree name, inside dest?"),
        ("dry run", "print + report\n(changes nothing)"),
        ("apply", "journal + fsync,\nthen link-move"),
        ("undo", "journal back to\noriginal paths"),
    ]
    x, y, bw, bh = 40, 230, 182, 150
    for i, (name, sub) in enumerate(boxes):
        bx = x + i * (bw + 22)
        fill = "#dcebe8" if name in ("dry run",) else "#ffffff"
        d.rounded_rectangle([bx, y, bx + bw, y + bh], radius=14, fill=fill, outline=ACCENT, width=3)
        d.text((bx + 16, y + 16), name, font=font(BOLD, 24), fill=INK)
        d.multiline_text((bx + 16, y + 62), sub, font=font(SANS, 17), fill=MUTED, spacing=6)
        if i < len(boxes) - 1:
            ax = bx + bw
            d.line([ax + 2, y + bh / 2, ax + 20, y + bh / 2], fill=ACCENT, width=3)
    d.text((60, 430), "The planner only reads. The executor only runs a finished plan.", font=font(SANS, 24), fill=INK)
    d.text((60, 470), "So the dry run can't disagree with the real run: it IS the plan.", font=font(SANS, 24), fill=INK)
    d.text(
        (60, 530),
        "Duplicates go to _duplicates/, never deleted. Moves can never overwrite.",
        font=font(SANS, 22),
        fill=MUTED,
    )
    caption(d, "Scan, find duplicates, plan. Nothing changes until you add --apply.")
    return img


def before_after(before: list[str], after: list[str]) -> Image.Image:
    img, d = canvas()
    heading(d, "Before and after: top level of the folder")
    f = font(MONO, 22)
    for x, label, items in ((80, "before", before), (680, "after --apply", after)):
        d.text((x, 110), label, font=font(BOLD, 26), fill=ACCENT)
        for i, name in enumerate(items[:18]):
            d.text((x, 160 + i * 28), clean(name), font=f, fill=INK)
    caption(d, "Category folders, clean names, copies quarantined. Undo puts it all back.")
    return img


# ------------------------------------------------------------------ scenes


def build_scenes(work: Path) -> list[tuple[Image.Image, float]]:
    res = {
        n: json.loads((ROOT / "evals" / "results" / f"{n}.json").read_text())
        for n in ("accuracy", "gold", "perf", "attacks")
    }
    a, g, p, k = res["accuracy"], res["gold"], res["perf"], res["attacks"]

    gen = run("generate", "messy", "--files", "1000", "--seed", "7", cwd=work)
    messy = work / "messy"
    listing = sorted(str(q.relative_to(messy)) for q in messy.rglob("*") if q.is_file() and not q.is_symlink())
    before = sorted(q.name + ("/" if q.is_dir() else "") for q in messy.iterdir() if not q.name.startswith("."))

    dry = run("organize", "messy", "--rename", "--report-dir", "reports", "--show", "12", cwd=work)
    applied = run("organize", "messy", "--rename", "--report-dir", "reports", "--show", "0", "--apply", cwd=work)
    after = sorted(q.name + ("/" if q.is_dir() else "") for q in messy.iterdir() if not q.name.startswith("."))
    report = json.loads((work / "reports" / "report.json").read_text())
    again = run("organize", "messy", "--rename", "--report-dir", "reports2", "--show", "0", cwd=work)
    journal = next((messy / ".file-engine").glob("journal-*.jsonl"))
    undone = run("undo", str(journal), "--apply", cwd=work)
    restored = sorted(q.name + ("/" if q.is_dir() else "") for q in messy.iterdir() if not q.name.startswith("."))
    assert restored == before, "undo did not restore the top level"

    strip = lambda s: [ln for ln in s.splitlines() if ln.strip()]  # noqa: E731
    gen_lines = [
        "$ file-engine generate messy --files 1000 --seed 7",
        *strip(gen),
        "",
        "$ find messy -type f | head",
        *listing[:14],
        f"... {len(listing) - 14} more",
    ]
    dry_lines = ["$ file-engine organize messy --rename", *strip(dry)]
    dry_lines = [re.sub(r"Review .*report.md", "Review reports/report.md", ln) for ln in dry_lines]
    app_lines = [
        "$ file-engine organize messy --rename --apply",
        *strip(applied),
        "",
        "$ file-engine organize messy --rename        # run it again",
        *strip(again)[:1],
        "",
        "$ file-engine undo messy/.file-engine/journal-....jsonl --apply",
        *strip(undone),
    ]
    app_lines = [
        re.sub(r"Undo: .*", "Undo:   file-engine undo messy/.file-engine/journal-....jsonl --apply", ln)
        for ln in app_lines
        if not ln.startswith(("  ...", "Report:"))
    ]

    d = report["duplicates"]
    report_rows = [
        ("Files scanned", f"{report['scanned']['files']:,}"),
        ("Skipped (hidden files, symlinks)", f"{sum(report['scanned']['skipped'].values())}"),
        ("Duplicate copies", f"{d['copies']} in {d['groups']} groups"),
        ("Reclaimable", f"{d['wasted_bytes'] / 2**20:.1f} MB"),
        (
            "Bytes read to find them",
            f"{d['bytes_read_to_find_them'] / 2**20:.1f} MB of {d['bytes_a_naive_full_hash_reads'] / 2**20:.1f} MB",
        ),
        ("Planned moves (all renamed)", f"{report['plan']['moves']}"),
        ("Copies to quarantine", f"{report['plan']['quarantined']}"),
        *[(f"  {c}", f"{v['files']} files") for c, v in list(report["categories"].items())[:5]],
    ]
    numbers = [
        ("Duplicate pair precision / recall", f"{a['dup_pair_precision']:.1%} / {a['dup_pair_recall']:.1%}"),
        ("Near-duplicate traps fooled", f"{a['traps_grouped'].replace('/', ' / ')}"),
        ("Gold-set categorisation (first run)", f"{g['right']} / {g['total']}  ({g['first_run']})"),
        ("Dry run left everything untouched", f"{a['dry_run_clean']} trees"),
        ("Data lost after apply", f"0 ({a['files']:,} files, {a['bytes'] / 1e9:.1f} GB)"),
        ("Idempotent / undo exact", f"{a['idempotent']} / {a['undo_exact']}"),
        (
            "Dry run, 5,014 files: p50 / p95",
            f"{p['plan_dry_run']['p50_ms']:.0f} / {p['plan_dry_run']['p95_ms']:.0f} ms",
        ),
        ("Bytes read vs hashing everything", f"{p['bytes_saved_share']:.0%} less"),
        ("Cost per task", "€0.00"),
        ("Attack success rate", f"{k['succeeded_with_all_defences']} / {k['attacks']}"),
    ]
    ablation = [
        (
            r["defence_off"].split(" (")[0],
            f"{r['succeeded']} / {r['tried']} get through" + ("  (next layer holds)" if r["succeeded"] == 0 else ""),
        )
        for r in k["ablation"]
    ]

    return [
        (title_card(), 10),
        (
            terminal(
                "generate a messy folder",
                gen_lines,
                "1,014 files: copies, near-copies, odd names, hidden files, symlinks.",
            ),
            16,
        ),
        (pipeline(), 18),
        (terminal("dry run (the default)", dry_lines, "The dry run lists every move. Nothing has changed yet."), 18),
        (
            table(
                "The dry-run report (reports/report.md)",
                report_rows,
                "Reports in Markdown, JSON and CSV. Found 180 copies by reading a third of the bytes.",
            ),
            16,
        ),
        (
            terminal(
                "apply, run again, undo",
                app_lines,
                "Apply moves 1,014 files. A second run has nothing to do. Undo restores everything.",
            ),
            18,
        ),
        (before_after(before, after), 14),
        (
            table(
                "Hard numbers (20 held-out trees, 40,280 files)",
                numbers,
                "Every number is measured against an answer key. Full table in the README.",
                col=700,
            ),
            22,
        ),
        (
            table(
                "Each defence switched off",
                ablation,
                "0 of 16 attacks get through. Switch a defence off and its attacks do.",
                col=700,
                size=20,
                highlight=False,
            ),
            18,
        ),
        (
            slide(
                "Postmortem: half the duplicates weren't removed",
                [
                    "First full run: 927 moved, 87 skipped",
                    "  every skip was a copy whose keeper had already moved",
                    "The safety re-check worked; the ORDER was wrong",
                    "Fix: quarantine every copy before moving any keeper",
                    "  copies handled 93/180 → 180/180; 2nd run 87 actions → 0",
                    "Also: the eval's own judge was wrong twice",
                ],
                "Found because the second run still had work to do.",
            ),
            20,
        ),
        (
            slide(
                "Next",
                [
                    "A fresh, unseen gold set of real file names",
                    "Hash cache (path, size, mtime) so re-runs skip unchanged files",
                    "Group-commit the journal fsync (apply ~3x faster)",
                    "Restore folder timestamps on undo",
                    "Watch mode for Downloads",
                ],
                "Design doc, postmortem and line-by-line cheatsheet are in the repo.",
            ),
            12,
        ),
    ]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        scenes = build_scenes(work)
        lst = work / "scenes.txt"
        entries = []
        for i, (img, secs) in enumerate(scenes):
            path = work / f"scene{i:02d}.png"
            img.save(path)
            entries.append(f"file '{path}'\nduration {secs}")
        entries.append(f"file '{work / f'scene{len(scenes) - 1:02d}.png'}'")  # concat needs the last frame twice
        lst.write_text("\n".join(entries) + "\n")
        for i, (img, _) in enumerate(scenes):
            img.save(ROOT / "docs" / "demo" / f"frame-{i:02d}.png") if i in (3, 7, 8) else None
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(lst),
                "-vf",
                "fps=30,format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-movflags",
                "+faststart",
                str(OUT),
            ],
            check=True,
        )
    total = sum(s for _, s in scenes)
    print(f"wrote {OUT.relative_to(ROOT)} ({total // 60}:{total % 60:02d})")


if __name__ == "__main__":
    main()
