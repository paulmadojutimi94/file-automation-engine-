# Learn: pathlib, hashing and safe automation, through this code

Three topics, each built from first principles, each pointing at the exact lines in this repo that use it, each ending with exercises. Work through them in order; each one takes about 30 minutes.

---

## Part 1: pathlib, or "stop gluing strings together"

### The idea

A file path looks like a string (`/Users/paul/Downloads/IMG_1.jpg`) but it isn't really one. It's a **list of folder names** with rules: separators differ between macOS (`/`) and Windows (`\`), `..` means "go up", and some names are special. Treating a path as a plain string is like treating a postal address as one long word: fine until you need just the postcode.

`pathlib.Path` is an object that knows those rules.

```python
from pathlib import Path

p = Path("/Users/paul/Downloads/Holiday Photo.JPG")
p.name  # 'Holiday Photo.JPG'   the last part
p.stem  # 'Holiday Photo'       name without the last extension
p.suffix  # '.JPG'                the last extension
p.parent  # Path('/Users/paul/Downloads')
p.parts  # ('/', 'Users', 'paul', 'Downloads', 'Holiday Photo.JPG')
p.parent / "sorted" / p.name  # the / operator joins paths safely
```

### The six things this repo does with pathlib

| What | Where | Why it matters |
|---|---|---|
| `Path(root).expanduser().resolve(strict=True)` | `scanner.py`, `engine.py` | `~` becomes your home folder; `resolve()` turns a relative path into an absolute one and follows symlinks; `strict=True` raises if it doesn't exist. Every later comparison is between absolute, canonical paths |
| `directory.iterdir()` | `scanner.py` | Lists one folder. We walk the tree ourselves with a **stack** instead of `rglob("*")`, so we decide what to skip and record *why* |
| `child.lstat()` vs `child.stat()` | `scanner.entry_stat` | `stat()` follows a shortcut to its target; `lstat()` describes the shortcut itself. Following shortcuts is how tools escape the folder you pointed them at (the attack eval proves it: `symlink_loop`) |
| `path.relative_to(root)` | `planner.py`, `report.py` | `Downloads/a.pdf` instead of the full path: used to rebuild folder structure inside `_duplicates/` |
| `path.resolve().is_relative_to(root.resolve())` | `safety.ensure_inside` | "Is this path really inside that folder, after following any symlinks?" This one line stops all three path-escape attacks |
| `path.open("xb")` | `executor._copy_no_clobber` | Mode `x` = **create, fail if it exists**. The safe way to never overwrite |

### Gotchas you'll be asked about

- **`Path("a.tar.gz").suffix` is `.gz`, not `.tar.gz`.** That's why `Rules.extension()` checks known compound extensions first.
- **`Path(".bashrc").suffix` is `""`.** A leading dot means hidden, not extension.
- **`exists()` follows symlinks.** A broken symlink "doesn't exist" but still occupies the name. That's why `resolve_collision` uses `os.path.lexists`.
- **Two names can be the same file.** On a Mac, `Report.pdf` and `report.pdf` are one file; so are `café` typed two different ways (NFC and NFD Unicode). `planner.collision_key` folds both before comparing.

### Exercises

1. In a Python shell, make `Path("~/Downloads/../Desktop/x.txt")` and call `.expanduser()`, then `.resolve()`. What happened to the `..`?
2. Create a symlink `ln -s / mylink` and compare `Path("mylink").stat().st_mode` with `.lstat().st_mode` using `stat.S_ISLNK`. Which one tells you it's a link?
3. Change `scanner.entry_stat` to use `stat()` and run `PYTHONPATH=src python3 evals/attacks.py`. Read which attack now succeeds and explain why in one sentence. (Then change it back.)

---

## Part 2: hashing, or "a fingerprint for a file"

### The idea

A **hash function** turns any amount of data into a short fixed-length fingerprint. SHA-256 always gives 64 hex characters:

```python
import hashlib

hashlib.sha256(b"hello").hexdigest()  # '2cf24dba5fb0a30e...'
hashlib.sha256(b"hellp").hexdigest()  # 'f4c3...' completely different
```

Three properties make it useful:

1. **Same input, same fingerprint.** Always, on every machine.
2. **Tiny change, totally different fingerprint.** Change one bit and about half the output bits flip.
3. **You can't find two inputs with the same fingerprint** (for SHA-256, nobody has ever managed it). MD5 and SHA-1 have lost this property: people can manufacture collisions on purpose, so we don't use them.

Think of it like a supermarket barcode, except the barcode is calculated *from the product itself*. Two tins with the same barcode are the same tin.

### Streaming: hashing a 4 GB file with 1 MB of memory

You don't need the whole file in memory. A hash object can be fed in pieces:

```python
h = hashlib.sha256()
with path.open("rb") as f:
    while chunk := f.read(1024 * 1024):  # 1 MiB at a time
        h.update(chunk)
h.hexdigest()
```

That's `hashing.full_digest`. The `:=` ("walrus") assigns and tests in one go: keep reading until `read()` returns empty bytes.

### The three sieves (why we don't just hash everything)

Hashing proves two files are identical, but it has to read every byte. Most files have no twin, so reading them is wasted work. The engine filters in stages, cheapest first, like a bouncer checking the guest list before checking ID:

1. **Size.** Free. A file with a unique size can't have a twin. Most files leave here.
2. **Edges.** Hash just the first and last 8 KiB. Files with different headers or endings leave here. For files of 16 KiB or less this *is* the whole file, so the result is final.
3. **Full SHA-256** for whatever is left.

Measured on 5,000 files (612 MB): **210 MB read instead of 612 MB**. The generator plants files designed to fool each sieve: same size but different content, same first and last 8 KiB but one different byte in the middle. 0 of 800 fooled it.

### Why the copy is re-hashed just before it's moved

The scan and the move can be minutes apart. If you edited the copy in between, it's no longer a duplicate. So `executor.verify_duplicate` hashes both the copy and the keeper again right before acting. Cheap insurance: the attack `duplicate_changed_after_plan` succeeds without it.

### Exercises

1. `printf 'a' > x; printf 'b' > y; shasum -a 256 x y`. Same size, different hash. Now `cp x z` and hash again.
2. In `hashing.py`, set `EDGE = 1` and run `make eval`'s accuracy part (`PYTHONPATH=src python3 evals/accuracy.py`). Does precision change? Does `bytes_read` change? Why is correctness unaffected? (Hint: what does sieve 3 do?)
3. Time `hashing.full_digest` on a 1 GB file with `CHUNK = 4 * 1024` and with `CHUNK = 1024 * 1024`. Explain the difference.

---

## Part 3: automation you can trust

Automation is a machine doing a chore while you're not watching. The risk is that it does the wrong thing *fast*. Five habits make it trustworthy, and each one is a piece of this code.

### 1. Plan, then act (dry run)

The planner decides everything and changes nothing; the executor only carries out a finished plan. The dry run is the plan, printed. Like a builder showing you drawings before knocking down a wall. Because both runs use the same plan, the dry run can't lie.

### 2. Never destroy, only move

No file is ever deleted or overwritten. Duplicates go to `_duplicates/`; you empty it when you're happy. `move_no_clobber` uses `os.link()` because, unlike `os.rename()`, it refuses to replace an existing file, and does so atomically (no gap between "check" and "act" for something else to sneak into).

### 3. Write it down first (write-ahead journal)

Before every move, the journal gets a line saying what's about to happen, and `fsync` forces it onto the disk. If the power cuts out mid-move, the journal already knows. Like a pilot's checklist: tick before you act, not after.

### 4. Check again right before acting

The world changes between plan and action. `stale_reason` asks "is this still the same regular file I planned for?" just before moving it.

### 5. Same input, same result (idempotency)

Running the tool twice must do nothing the second time. That's why renaming strips an existing date prefix before adding one (otherwise you'd get `2026-11-11_2026-11-11_photo.jpg`), and why files already in the right place claim their names first. The eval checks it on every tree: 20/20.

### Exercises

1. Run `make demo`. Then run the organize command again with `--apply`. How many moves? Why?
2. Apply a run, then open `.file-engine/journal-*.jsonl`. Edit one `src` to `/tmp/elsewhere.txt` and run `undo` (dry run). What does it say, and which function said it?
3. Write a test: plan, then create the destination file yourself, then apply. Assert your file is untouched. (There's one already; write yours before looking at `test_move_never_overwrites`.)
4. Explain to a friend, in two sentences each, why `exists()` then `rename()` is unsafe, and why `fsync` makes apply 3× slower.
