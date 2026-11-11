# Design: file-automation-engine

**Author:** Paul Madojutimi · **Status:** v0.1 shipped · **Reading time:** 5 minutes

## Problem

People's Downloads and Desktop folders fill up with `IMG_0412 (1).JPG`, `Copy of report FINAL v2.pdf` and five copies of the same installer. Tidying by hand is slow and boring, and most scripts that do it for you are dangerous: they overwrite files with the same name, follow shortcuts out of the folder, delete "duplicates" that were only similar, and can't be undone.

**Goal:** one command that scans a folder, finds true duplicates, renames and sorts everything into category folders, and writes a report, **without ever losing a byte**, and with a dry run first and an undo after.

**Non-goals for v0.1:** watching folders live, sorting by what's *inside* a file (OCR, image content), cloud drives, Windows-specific behaviour.

## Requirements, ranked

1. **Never lose data.** No overwrites, no deletes, every move undoable. Measured: 0 bytes lost on 40,280 files; undo exact on 20/20 trees.
2. **Dry run by default.** Changing files needs `--apply`. Measured: the dry run left every byte, file timestamp and folder timestamp untouched on 20/20 trees.
3. **Right answers.** No false duplicates (precision 100%), no missed ones (recall 100%), files in the folder a person would expect.
4. **Safe on hostile names.** File names are text other people control (downloads, shared drives, zip files). 0/16 attacks succeed.
5. **Fast enough not to think about.** 5,000 files planned in under half a second.

## Key decisions and the alternatives rejected

### 1. Plan, then apply (instead of "do it as you go")

The engine is split into a **planner** that only reads the disk and an **executor** that only carries out a finished plan. The dry run *is* the plan, printed. `--apply` hands the same plan to the executor.

| Option | Why not |
|---|---|
| Move files while scanning | Can't show a dry run that matches what will happen; a crash halfway leaves no record |
| A `--dry-run` flag checked inside each move | Two code paths that can drift apart: the classic "dry run said X, real run did Y" bug |
| **Plan object, separate executor** ✅ | Dry run and real run share 100% of the decision code; the plan is testable without touching disk |

### 2. Duplicates: three sieves, cheapest first

To know two files are identical you must read every byte of both. The trick is to read as few files as possible:

1. **Size** (free, the scanner already has it). A file with a unique size can't have a twin.
2. **Edge digest:** SHA-256 of the first and last 8 KiB. For files up to 16 KiB this *is* the whole file.
3. **Full SHA-256**, only for files that still match.

| Option | Why not |
|---|---|
| Compare names | `holiday.jpg` and `IMG_4411.jpg` can be the same photo; two `notes.txt` can differ |
| Size only | The generator plants same-size files with different content: all would be "duplicates" |
| MD5 / file modified time | MD5 collisions can be manufactured on purpose; mtimes change on copy |
| SHA-256 of everything | Correct but reads every byte. On the 5,000-file eval tree it reads 612 MB, the sieves read 210 MB (66% less) |
| Byte-by-byte comparison | Needs files opened in pairs; no fingerprint to report. SHA-256 collisions are not a practical risk |
| **Size → edges → SHA-256** ✅ | 100% precision and recall; 66% less reading |

Measured like for like on that tree with files already cached: the sieves take **203 ms** (p50), hashing everything **583 ms**, so ~2.9× faster even when reading is nearly free. On a cold disk, USB stick or network drive the gap grows, because there reading *is* the cost. (An earlier draft of this doc said "only 1.4× faster": it compared the whole dry run, scanning and planning included, against hashing alone. See the postmortem.)

### 3. Duplicates are quarantined, never deleted

Copies move to `_duplicates/` with their original folder structure, so you can see where each came from and delete the folder yourself when happy. The **keeper** is the copy whose name doesn't look like a copy (`(1)`, `Copy of`, ` copy`), then the oldest, then the shortest path.

Rejected: deleting (irreversible); hard-linking duplicates together to save space (editing one silently edits "both", which surprises people).

### 4. Moving without overwriting: `os.link` then `unlink`

`os.rename()` **silently replaces** an existing file on Linux and macOS. Checking `exists()` first leaves a gap in which another program can create the file. So the executor creates the new name with `os.link()` (an atomic step that *fails* if the name is taken) and only then removes the old name. Across disks, where hard links are impossible, it copies into a file opened with mode `"x"` (create, fail if it exists), checks the size, then deletes the source.

Rejected: `shutil.move` (overwrites), `exists()`-then-`rename` (race), Linux-only `renameat2(RENAME_NOREPLACE)` (not in Python's standard library, not on macOS).

### 5. A write-ahead undo journal, with fsync

Before each move, one JSON line `{src, dst}` is written to `.file-engine/journal-*.jsonl` and **fsync'd** to disk. If the power goes out mid-run, the journal already lists every move that might have happened, and `undo` works out from the disk which ones did.

Cost, measured: fsync is about 70% of apply time (5,000 moves: ~2.0 s with fsync, ~0.6 s without). That's the price of crash-safety, and 2 seconds is fine for a tidy-up. Group commit (fsync every 100 moves) is in next steps if it ever matters.

### 6. Re-check everything just before acting

The plan might be minutes old. Just before each move the executor checks the source is still a regular file with the same size and mtime (not swapped for a symlink), and before quarantining a "duplicate" it **re-hashes both the copy and the keeper**. A copy you edited after the scan is no longer a duplicate and stays put. The attack eval shows each check stopping a real attack when the other defences are on.

### 7. Category by extension, with a name table

`Rules` maps extensions to folders (`.jpg` → `Images`), handles compound extensions (`.tar.gz`), and knows a few extensionless names (`Makefile`, `Dockerfile`). Users can supply their own JSON.

Rejected: sniffing file contents with "magic bytes" (needs a dependency, slow, and people sort by what a file *is for*, which the extension says better); machine-learning classification (no training data, no way to explain a wrong answer).

## Risks

| Risk | Mitigation |
|---|---|
| Generated test data shares the author's blind spots | Hand-written gold set (first run 32/40 exposed exactly that); attack set written separately |
| Hidden folder rules on macOS/Windows (case-insensitive, accent-insensitive names) | Name collisions are checked case- and accent-insensitively on every platform |
| Someone runs it on `~` | Dry run by default, tool folders (`.git`, `node_modules`) and hidden files skipped, undo journal |
| A tampered journal could move files anywhere on undo | Undo trusts the journal's location, not its contents; every path must stay inside the original folders; undo is a dry run by default |

## How we'll know it works

The evals in `evals/` (results table in the README), re-run in CI on every push.
