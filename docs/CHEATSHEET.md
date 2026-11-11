# Cheatsheet: every line, explained

Read this before an interview. It walks the code in the order data flows through it: **models → scanner → hashing → rules → naming → planner → executor → undo → safety → report → engine → cli → generator**. Each block quotes the code and says what each line does and why it's written that way. The "Interview drill" at the end has the questions you're most likely to get.

Conventions used everywhere:

- `from __future__ import annotations` (top of every file): type hints are stored as strings and not evaluated at import time, so a class can mention a type defined later in the file, and imports stay cheap.
- `@dataclass(frozen=True, slots=True)`: `dataclass` writes `__init__`, `__repr__` and `__eq__` for you; `frozen=True` makes instances read-only (a planned `Action` can't be changed by accident after planning); `slots=True` stores fields in a fixed layout instead of a per-object dict: less memory and faster attribute access, which matters with 40,000 `FileRecord`s.

---

## models.py: the nouns

```python
@dataclass(frozen=True, slots=True)
class FileRecord:
    path: Path
    size: int
    mtime: float
```
One regular file as the scanner saw it. `mtime` is the "last modified" time in seconds since 1970. Size and mtime are kept so the executor can later ask "is this still the file I planned for?"

```python
class Skipped:
    path: Path
    reason: str
```
Everything we deliberately left alone, with a reason (`"symlink"`, `"hidden"`). Reports show these so nothing disappears silently.

```python
class ScanResult: root, files, skipped, dirs_seen
    @property
    def total_bytes(self): return sum(f.size for f in self.files)
```
`field(default_factory=list)` gives each instance its own new list. (A plain `= []` default would be shared between instances, a classic Python bug, and dataclasses refuse it.) `@property` makes `total_bytes` look like an attribute but compute on demand.

```python
class DuplicateGroup: digest, keeper, copies
    wasted_bytes = keeper.size * len(copies)
```
Files with identical content. `keeper` stays; `copies` go to quarantine. `copies` is a `tuple` (immutable) to match `frozen=True`.

```python
class DuplicateResult:
    groups, bytes_read, files_edge_hashed, files_full_hashed, errors
```
The groups plus the counters the README reports (bytes read vs. naive).

```python
class ActionKind(StrEnum):
    ORGANIZE = "organize"
    QUARANTINE = "quarantine"
```
`StrEnum` (Python 3.11+): enum members that are also strings, so they print and JSON-serialise as `"organize"` without `.value` gymnastics, while code compares `kind is ActionKind.QUARANTINE` (no typo-prone strings).

```python
class Action: kind, src, dst, size, mtime, category, digest=None, keeper=None
```
One planned move. `digest` and `keeper` are filled only for quarantines: the executor needs them to prove, just before moving, that the copy and keeper still match.

---

## scanner.py: find every file, follow no shortcuts

```python
ALWAYS_IGNORED = frozenset(
    {".git", ".hg", ".svn", "__pycache__", "node_modules", ".DS_Store", "Thumbs.db", "desktop.ini"}
)
```
Tool-owned files. Moving `.git` would break a repository. `frozenset` = an immutable set: O(1) membership tests, and it can't be modified by accident at runtime.

```python
def entry_stat(path: Path) -> os.stat_result:
    return path.lstat()
```
`lstat` describes the directory entry itself; for a symlink it says "symlink". `stat` would follow the link and describe the target. It's a separate function so the attack eval can swap it for `stat()` and prove the difference (`symlink_loop` then hides your only copy of a file in `_duplicates/`).

```python
def scan(root, *, include_hidden=False, exclude=()) -> ScanResult:
```
The `*` makes everything after it keyword-only: callers must write `include_hidden=True`, which reads clearly and can't be confused by argument order.

```python
root = Path(root).expanduser().resolve(strict=True)
if not root.is_dir():
    raise NotADirectoryError(root)
excluded = {Path(p).resolve() for p in exclude}
```
Normalise once: `~` expanded, absolute, symlinks in the root path resolved, and an error if it doesn't exist. Exclusions (the engine's own `_duplicates/` and journal folder, and the reports folder) are resolved the same way so comparisons work.

```python
    stack = [root]
    while stack:
        directory = stack.pop()
        result.dirs_seen += 1
```
Iterative depth-first walk. A recursive function would hit Python's recursion limit (~1,000) on deep trees; an explicit list has no limit.

```python
        try:
            children = list(directory.iterdir())
        except OSError as exc:
            result.skipped.append(Skipped(directory, f"unreadable_dir ({exc.strerror})"))
            continue
```
A folder we can't read (permissions) is recorded and skipped, never a crash. `OSError` is the parent of `PermissionError`, `FileNotFoundError` (folder deleted mid-scan), etc.

```python
        for child in children:
            if name in ALWAYS_IGNORED: skipped "tool_file"; continue
            if child in excluded: continue
            if not include_hidden and name.startswith("."): skipped "hidden"; continue
```
Order matters: the engine's own folders are excluded *silently* before the hidden check, otherwise `.file-engine` showed up as "skipped: hidden" on every re-run (found and fixed; see postmortem).

```python
            try: st = entry_stat(child)
            except OSError as exc: skipped "unreadable"; continue
```
One `lstat` call per entry, and every decision below uses that one result, so the answer can't change between two checks.

```python
            if stat.S_ISLNK(st.st_mode): skipped "symlink"
            elif stat.S_ISDIR(st.st_mode): stack.append(child)
            elif stat.S_ISREG(st.st_mode): result.files.append(FileRecord(child, st.st_size, st.st_mtime))
            else: skipped "special_file"
```
`st_mode` packs the file type and permissions into one integer; the `stat.S_IS*` helpers test the type bits. Only regular files are ever touched; sockets, pipes and devices are skipped.

```python
    result.files.sort(key=lambda f: f.path)
```
Deterministic order: the same tree always gives the same plan, which makes runs reproducible and tests stable.

---

## hashing.py: duplicates, reading as little as possible

```python
EDGE = 8 * 1024
CHUNK = 1024 * 1024
```
8 KiB from each end for sieve 2; 1 MiB reads for full hashing (big enough to keep syscalls rare, small enough that memory stays flat on a 10 GB file).

```python
def full_digest(path):
    h = hashlib.sha256()
    n = 0
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n
```
Streaming hash. `"rb"` = read binary (no text decoding, no newline translation). The walrus `:=` assigns and tests: the loop ends when `read` returns `b""` at end of file. Returns bytes read too, so the caller can detect a file that changed size.

```python
def edge_digest(path, size):
    with path.open("rb") as f:
        if size <= 2 * EDGE:
            data = f.read()
            if len(data) != size:
                raise ChangedWhileReading(...)
            return hashlib.sha256(data).hexdigest(), len(data)
        head = f.read(EDGE)
        f.seek(-EDGE, os.SEEK_END)
        tail = f.read(EDGE)
        if f.tell() != size:
            raise ChangedWhileReading(...)
    h = hashlib.sha256(head)
    h.update(tail)
    return h.hexdigest(), len(head) + len(tail)
```
Small file: read it all, so this digest **is** the full SHA-256 and sieve 3 is skipped for it. Large file: first 8 KiB, then `seek` to 8 KiB before the end (`SEEK_END` = relative to the end) and read the last 8 KiB. `f.tell()` after the last read is the current file length: if it differs from the scanned size, the file changed under us and we refuse to guess.

```python
_COPY_NAME = re.compile(r"(?i)(^copy of |[\s_-]copy(\s*\d+)?$|\s\(\d+\)$)")
```
`(?i)` = case-insensitive. Three alternatives: starts with "copy of ", ends with " copy"/"-copy 2", or ends with " (1)". Compiled once at import, reused for every file.

```python
def keeper_key(record):
    return (bool(_COPY_NAME.search(record.path.stem)), record.mtime, len(str(record.path)), str(record.path))
```
Python compares tuples left to right, and `False < True`, so: original-looking names first, then oldest, then shortest path, then alphabetical as a final tie-break so the choice is always the same.

```python
def find_duplicates(files, *, min_size=1):
    by_size = defaultdict(list)
    for f in files:
        if f.size >= min_size:
            by_size[f.size].append(f)
```
Sieve 1. `defaultdict(list)` creates an empty list the first time a key is used. `min_size=1` leaves empty files out: they're all "identical" and removing them helps nobody.

```python
for size, same_size in sorted(by_size.items()):
    if len(same_size) < 2:
        continue
```
A unique size can't have a twin: zero bytes read for it. This is where most files leave.

```python
by_edge = defaultdict(list)
for f in same_size:
    try:
        digest, n = edge_digest(f.path, size)
    except OSError as exc:
        result.errors.append(...)
        continue
    result.bytes_read += n
    result.files_edge_hashed += 1
    by_edge[digest].append(f)
```
Sieve 2, only among same-size files. Unreadable files become errors in the report, not crashes.

```python
for edge, same_edge in by_edge.items():
    if len(same_edge) < 2:
        continue
    if size <= 2 * EDGE:
        result.groups.append(_make_group(edge, same_edge))
        continue
```
Small files: the edge digest already covered every byte, so this is a confirmed group.

```python
            by_full = defaultdict(list)
            for f in same_edge:
                digest, n = full_digest(f.path)
                if n != size: errors "size changed"; continue
                ...
                by_full[digest].append(f)
            for digest, members in by_full.items():
                if len(members) > 1: result.groups.append(_make_group(digest, members))
```
Sieve 3: full SHA-256 only for survivors. Only groups with 2+ members are duplicates.

```python
    result.groups.sort(key=lambda g: (-g.wasted_bytes, str(g.keeper.path)))
```
Biggest wins first in the report (`-` sorts descending), ties alphabetical.

---

## rules.py: which folder?

```python
DEFAULT_CATEGORIES = {"Images": (".jpg", ...), ..., "Archives": (..., ".tar.gz", ...)}
```
Wrapped in `# fmt: off` / `# fmt: on` so the formatter doesn't explode it into one extension per line.

```python
KNOWN_NAMES = {name: "Code" for name in ("dockerfile", "makefile", ...)}
```
A dict comprehension. Files recognised by their whole name (they have no extension). Added after the gold set's first run missed `Makefile` and `Dockerfile`.

```python
class Rules:
    def __init__(self, categories=None):
        categories = DEFAULT_CATEGORIES if categories is None else categories
```
`None` as the default, never the dict itself, so a caller can't accidentally mutate the shared default.

```python
self.by_ext = {}
self.categories = set(categories)
for category, exts in categories.items():
    if not safety.is_safe_component(category) or category.startswith("."):
        raise ValueError(...)
```
Category names become folder names, so `"../../etc"` or `".hidden"` is rejected here (a rules file can come from someone else).

```python
for ext in exts:
    ext = ext.lower() if ext.startswith(".") else "." + ext.lower()
    if self.by_ext.get(ext, category) != category:
        raise ValueError("listed under both")
    self.by_ext[ext] = category
```
Accept `"jpg"` or `".JPG"`; store `.jpg`. `.get(ext, category)` returns `category` if unseen, so the check only fires when an extension is claimed by two different folders.

```python
        self.compound = sorted((e for e in self.by_ext if e.count(".") > 1), key=len, reverse=True)
```
`.tar.gz`-style extensions, longest first, so `.tar.gz` wins over `.gz`.

```python
@classmethod
def from_json(cls, path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(...):
        raise ValueError(...)
    return cls(data)
```
`@classmethod` = an alternative constructor (`Rules.from_json("r.json")`). The shape is validated before use; JSON from disk is untrusted.

```python
def extension(self, name):
    lower = name.lower()
    for ext in self.compound:
        if lower.endswith(ext) and len(lower) > len(ext):
            return ext
    dot = lower.rfind(".")
    if dot <= 0 or dot == len(lower) - 1:
        return ""
    return lower[dot:]
```
`rfind` = last dot. `dot <= 0` covers "no dot" (`-1`) and a leading dot (`.bashrc`, position 0: hidden, not an extension). A trailing dot (`name.`) has no extension. `len(lower) > len(ext)` stops a file literally named `.tar.gz` counting.

```python
def category(self, name):
    known = KNOWN_NAMES.get(name.lower())
    if known in self.categories:
        return known
    return self.by_ext.get(self.extension(name), OTHER)
```
The name table only applies if the rules have that folder (custom rules without "Code" ignore it). Anything unknown goes to `Other`.

---

## naming.py: clean names

```python
DEFAULT_TEMPLATE = "{date}_{name}"
TEMPLATE_FIELDS = frozenset({"date", "name", "category"})
MAX_NAME_BYTES = 255
_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}_")
_RUNS = re.compile(r"[-_]*-[-_]*")
```
255 is a **byte** limit, not a character limit: `é` is 2 bytes in UTF-8, `日` is 3, an emoji 4. `_RUNS` matches any run of `-`/`_` that contains at least one `-`, so `"a - b"` → `"a---b"` → `"a-b"` but `thesis_v4` keeps its underscore.

```python
def slugify(stem):
    stem = unicodedata.normalize("NFC", stem).lower()
    chars = [ch if (ch.isalnum() or ch in "_-") else "-" for ch in stem]
    slug = _RUNS.sub("-", "".join(chars)).strip("-_")
    return slug or "file"
```
NFC normalisation: `é` can be stored as one code point or as `e` + a combining accent; NFC picks the single form so equal-looking names are equal strings. `isalnum()` is Unicode-aware, so `résumé` and `日本語` survive. Everything else (spaces, brackets, emoji, `!`) becomes `-`; runs collapse; ends are trimmed. A name of only symbols becomes `file`.

```python
def validate_template(template):
    for _literal, field, spec, conv in string.Formatter().parse(template):
        if field is None:
            continue
        if field not in TEMPLATE_FIELDS or spec or conv:
            raise ValueError(...)
    literal = template.replace("{", "").replace("}", "")
    if not safety.is_safe_component(literal or "x") or literal.startswith("."):
        raise ValueError(...)
```
`Formatter().parse` splits a format string into `(literal text, field name, format spec, conversion)`. Only bare `{date}`, `{name}`, `{category}` pass: `{name.__class__}` (attribute access, a known format-string injection), `{name!r}` and `{date:%Y}` are refused. The literal text must be one path segment that doesn't start with a dot: no `../`, no `/`, no hidden output.

```python
def fit_name(stem, ext, suffix=""):
    budget = MAX_NAME_BYTES - len(suffix.encode()) - len(ext.encode())
    raw = stem.encode("utf-8")
    if len(raw) > budget:
        stem = raw[:budget].decode("utf-8", errors="ignore").rstrip("-_ ")
    return f"{stem}{suffix}{ext}"
```
Measure in bytes, cut in bytes, then decode with `errors="ignore"`, which drops a half-cut multi-byte character instead of crashing. The extension and the `-2` collision suffix are always kept.

```python
def split_name(name, rules):
    ext_len = len(rules.extension(name))
    return (name[:-ext_len], name[-ext_len:]) if ext_len else (name, "")
```
Uses the rules' idea of the extension (so `.tar.gz` is one piece) but keeps the original case. The `if ext_len` matters: `name[:-0]` is `""`, not the whole name.

```python
def clean_name(record, rules, template, category):
    stem, ext = split_name(record.path.name, rules)
    slug = slugify(stem)
    if "{date}" in template:
        slug = _DATE_PREFIX.sub("", slug) or "file"
    date = datetime.fromtimestamp(record.mtime).strftime("%Y-%m-%d")
    new_stem = template.format(date=date, name=slug, category=category.lower())
    return new_stem, ext.lower()
```
Strip an existing `YYYY-MM-DD_` before adding one: this is what makes renaming idempotent. The date is the file's last-modified date in local time; moving or hard-linking a file keeps its mtime, so the date is stable across runs. Extensions are lower-cased (`.JPG` → `.jpg`).

---

## planner.py: decide everything, change nothing

```python
QUARANTINE_DIR = "_duplicates"
STATE_DIR = ".file-engine"
```

```python
@dataclass(slots=True)
class Plan:
    root, dest, scan, duplicates
    actions = []
    unchanged = 0
    category_files = Counter()
    category_bytes = Counter()
    refused = []
    moves / quarantines / renames(properties)
```
Not frozen: it's built up step by step. `Counter` is a dict that defaults missing keys to 0 and has `.most_common()`.

```python
@dataclass(slots=True)
class _Wanted:
    record, kind, target, stem, ext, category, group = None
```
Leading underscore = private to this module. Where each file *wants* to go before collisions are resolved.

```python
def collision_key(path):
    return unicodedata.normalize("NFC", str(path)).casefold()
```
`casefold()` is a stronger `lower()` for comparisons (`ß` → `ss`). Two paths with the same key could be one file on macOS/Windows, so they must not both be used. Applied on every platform, so a folder organised on Linux is still safe to copy to a Mac.

```python
def resolve_collision(target, stem, ext, claimed):
    candidate, n = target, 1
    while collision_key(candidate) in claimed or os.path.lexists(candidate):
        n += 1
        candidate = target.with_name(naming.fit_name(stem, ext, f"-{n}"))
    return candidate
```
Try `name.ext`, `name-2.ext`, `name-3.ext`, … until one is neither claimed by this plan nor already on disk. `lexists` (not `exists`) so a broken symlink still counts as taken. `with_name` swaps just the last segment.

```python
def build_plan(scan, duplicates, *, dest, rules, rename, template=DEFAULT_TEMPLATE):
    dest = dest.resolve()
    plan = Plan(...)
    copy_of = {c.path: g for g in duplicates.groups for c in g.copies}
```
A dict comprehension with two `for`s: maps every copy's path to its group, for O(1) "is this file a copy?" lookups.

```python
    for rec in scan.files:
        category = rules.category(rec.path.name)
        plan.category_files[category] += 1; plan.category_bytes[category] += rec.size
        group = copy_of.get(rec.path)
        if group is not None:
            target = dest / QUARANTINE_DIR / rec.path.relative_to(scan.root)
            ... wanted.append(_Wanted(rec, QUARANTINE, target, stem, ext, category, group)); continue
        stem, ext = naming.clean_name(...) if rename else naming.split_name(...)
        name = naming.fit_name(stem, ext)
        wanted.append(_Wanted(rec, ORGANIZE, dest / category / name, stem, ext, category))
```
Copies keep their original relative path inside `_duplicates/` so you can see where each came from. Everything else goes to `dest/<Category>/<name>`.

```python
claimed = set()
pending = []
for w in wanted:
    if w.target == w.record.path:
        claimed.add(collision_key(w.target))
        plan.unchanged += 1
    else:
        pending.append(w)
```
**Pass 1.** Files already in the right place claim their names first. Without this, a file processed earlier could take a settled file's name and push it to `-2` on a re-run: not idempotent.

```python
for w in pending:
    target = resolve_collision(w.target, w.stem, w.ext, claimed)
    try:
        if not safety.is_safe_component(target.name):
            raise safety.UnsafePathError(...)
        safety.ensure_inside(target, dest)
    except safety.UnsafePathError as exc:
        plan.refused.append((w.record.path, str(exc)))
        continue
    claimed.add(collision_key(target))
    plan.actions.append(Action(...))
```
**Pass 2.** Everyone else gets the first free name. Then the containment check: if the target would land outside `dest` (a traversal template, or a category folder that's secretly a symlink), the file is **refused** and shown under "Needs a human" rather than moved.

```python
    plan.actions.sort(key=lambda a: a.kind is not ActionKind.QUARANTINE)
    return plan
```
`False` sorts before `True`, so quarantines come first. `sort` is stable, so path order is kept within each kind. This line is the fix for Incident 1 in the postmortem.

---

## executor.py: the only code that moves files

```python
@dataclass(frozen=True, slots=True)
class Outcome: action, status, detail=""
class ApplyResult: journal, outcomes; counts -> Counter(status)
```

```python
def _copy_no_clobber(src, dst):
    try:
        with src.open("rb") as fin, dst.open("xb") as fout:
            shutil.copyfileobj(fin, fout, hashing.CHUNK)
        shutil.copystat(src, dst)
        if dst.stat().st_size != src.stat().st_size:
            raise OSError(errno.EIO, ...)
    except FileExistsError:
        raise
    except BaseException:
        dst.unlink(missing_ok=True)
        raise
    src.unlink()
```
Cross-disk fallback. `"xb"` = exclusive create: fails if `dst` exists, atomically. `copyfileobj` streams in 1 MiB chunks. `copystat` copies the modified time and permissions, so dates (and `{date}` renames) don't change. Size check before deleting the source. `except FileExistsError: raise` first, so we never delete a file that isn't ours; `except BaseException` (which includes Ctrl-C) removes our half-written copy. The source is deleted only after everything succeeded.

```python
def move_no_clobber(src, dst):
    try:
        os.link(src, dst, follow_symlinks=False)
    except FileExistsError:
        raise
    except OSError as exc:
        if exc.errno in (errno.EXDEV, errno.EPERM, errno.ENOTSUP, errno.EMLINK, errno.EACCES):
            _copy_no_clobber(src, dst)
            return
        raise
    os.unlink(src)
```
A hard link is a second name for the same data. `os.link` creates it atomically and **fails if `dst` exists**; then `unlink` removes the old name. Net effect: a move that can never overwrite. `os.rename` would silently replace `dst`. `EXDEV` = "cross-device link" (different disk); the others are filesystems that don't do hard links: fall back to copying. `follow_symlinks=False`: if the source were somehow a symlink, link the link, not its target (third layer of symlink defence; the attack eval shows it).

```python
def stale_reason(action):
    try:
        st = action.src.lstat()
    except FileNotFoundError:
        return "source no longer exists"
    if not stat.S_ISREG(st.st_mode):
        return "source is no longer a regular file"
    if st.st_size != action.size or st.st_mtime != action.mtime:
        return "source changed since the plan was made"
    return None
```
Plans go stale. Re-check type (catches a file swapped for a symlink), size and mtime right before acting. Returns a reason string or `None`, so callers can report *why*.

```python
def verify_duplicate(action):
    for path in (action.src, action.keeper):
        if path.is_symlink():
            return ...
        digest, _ = hashing.full_digest(path)
        if digest != action.digest:
            return f"{path.name} changed since scan; no longer a duplicate"
    return None
```
Re-hash **both** the copy and the keeper. A copy edited since the scan isn't a duplicate any more; a keeper that changed or vanished means there'd be no identical file left behind. This is the check that exposed Incident 1.

```python
def _log(journal, entry):
    journal.write(json.dumps(entry, ensure_ascii=False) + "\n")
    journal.flush()
    os.fsync(journal.fileno())
```
JSON Lines: one JSON object per line, appendable, readable even if the last line is cut off. `flush` moves Python's buffer to the OS; `fsync` forces the OS to write it to the physical disk. Without `fsync` a power cut can lose lines the OS was still holding. Cost: ~70% of apply time.

```python
def _make_dirs(directory, dest, journal):
    missing = []
    d = directory
    while not d.exists() and d != dest:
        missing.append(d)
        d = d.parent
    for d in reversed(missing):
        d.mkdir()
        _log(journal, {"op": "mkdir", "path": str(d)})
```
Like `mkdir -p`, but each folder we create is journaled so undo can remove it again. Walk up until something exists, then create top-down.

```python
def _apply_one(action, plan, journal):
    reason = stale_reason(action)
    if reason:
        return Outcome(action, "skipped", reason)
    if action.kind is ActionKind.QUARANTINE:
        reason = verify_duplicate(action)
        if reason:
            return Outcome(action, "skipped", reason)
    try:
        safety.ensure_inside(action.dst, plan.dest)
        _make_dirs(action.dst.parent, plan.dest, journal)
        safety.ensure_inside(action.dst, plan.dest)
        _log(journal, {"op": "move", "src": ..., "dst": ...})
        move_no_clobber(action.src, action.dst)
    except FileExistsError:
        return Outcome(action, "skipped", "destination appeared after planning; not overwritten")
    except safety.UnsafePathError as exc:
        return Outcome(action, "failed", str(exc))
    except OSError as exc:
        return Outcome(action, "failed", ...)
    return Outcome(action, "moved")
```
The order is the safety argument: re-check → (re-hash) → containment → make folders → containment again (a folder could have been swapped for a symlink in between) → **journal, then move**. Every failure becomes an `Outcome` with a reason; one bad file never stops the run.

```python
def apply_plan(plan):
    state = plan.dest / STATE_DIR
    state.mkdir(parents=True, exist_ok=True)
    journal_path = state / f"journal-{datetime.now():%Y%m%d-%H%M%S-%f}.jsonl"
    with journal_path.open("x", encoding="utf-8") as journal:
        _log(journal, {"op": "header", "version": 1, "root": ..., "dest": ..., "created": ...})
        for action in plan.actions:
            result.outcomes.append(_apply_one(action, plan, journal))
```
`%f` = microseconds, so two runs never share a journal; `"x"` mode would refuse if they did. The header records the version (so a future format can be detected) and the root that undo checks paths against.

---

## undo.py: reverse a run

```python
def check_entry(src, dst, root, dest):
    if not (src.is_absolute() and dst.is_absolute()):
        return "relative path in journal"
    if not _inside(dst, dest):
        return "moved-to path is outside the organised folder"
    if not _inside(src, root):
        return "restore path is outside the original folder"
    return None
```
A journal is just a text file someone could edit. Every line must move a file from inside `dest` back to inside `root`. Without this, a tampered journal could pull an outside file in, or plant a file anywhere (both proven by the attack eval).

```python
def undo(journal_path, *, apply=False):
    lines = [json.loads(line) for line in ...splitlines() if line]
    if not lines or lines[0].get("op") != "header": raise ValueError
    if any(e.get("op") == "undone" for e in lines): raise ValueError("already undone")
    if journal_path.parent.name != STATE_DIR: raise ValueError
    dest = journal_path.parent.parent
    root = Path(lines[0]["root"])
```
`dest` comes from **where the journal is stored**, not from what it says. A journal can only ever be undone once.

```python
    for entry in reversed([e for e in lines if e.get("op") == "move"]):
        problem = check_entry(...)
        if problem:                                   -> "refused"
        elif lexists(src) and not lexists(dst):       -> "not_moved"  (journaled, but the move was skipped)
        elif not lexists(dst):                        -> "missing"    (you moved or deleted it since)
        elif lexists(src):                            -> "failed"     (something new is at the old path)
        elif not apply:                               -> "would_restore"
        else: src.parent.mkdir(parents=True, exist_ok=True); executor.move_no_clobber(dst, src) -> "restored"
```
Reverse order, so later moves are undone first. Because the journal line is written *before* each move, some lines may describe moves that never happened (skipped, or a crash mid-move); the disk state decides which. Restoring uses the same never-overwrite move. Dry run by default, like `organize`.

```python
    if apply:
        for entry in reversed(mkdir entries):
            if _inside(d, dest) and d.is_dir() and not any(d.iterdir()): d.rmdir()
        append {"op": "undone"}
```
Remove folders the run created, only if empty (`any(d.iterdir())` stops at the first entry, so it's cheap). Then mark the journal undone.

---

## safety.py: every defence in one place

```python
def is_safe_component(name):
    return name not in {"", ".", ".."} and "/" not in name and "\\" not in name and "\x00" not in name
```
Something that can only ever be **one** path segment. `\x00` (NUL) truncates paths in C code underneath Python.

```python
def ensure_inside(path, root):
    if not path.resolve().is_relative_to(root.resolve()):
        raise UnsafePathError(...)
```
`resolve()` collapses `..` and follows symlinked folders, so both `Documents/../../x` and a `Documents -> /etc` symlink are caught.

```python
def display(text):
    for ch in text:
        if unicodedata.category(ch).startswith("C"): escape as \xNN or \uNNNN
```
Unicode category `C*` = control (`Cc`: ESC, newline), format (`Cf`: right-to-left override U+202E, zero-width chars), surrogates, private use, unassigned. Printing ESC lets a file name rewrite your terminal; U+202E makes `invoice\u202efdp.exe` look like `invoiceexe.pdf`.

```python
def md_cell(text):
    text = html.escape(display(text), quote=False)
    return "".join("\\" + ch if ch in _MD_SPECIAL else ch for ch in text)
```
`html.escape` turns `<` into `&lt;` (no HTML tags in the report); backslash-escaping `|` stops a file name adding table columns, and `*_[]#` stop it formatting the report.

```python
def csv_cell(text):
    text = display(text)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text
```
Excel and Google Sheets run a cell starting with `=`, `+`, `-` or `@` as a formula (`=HYPERLINK(...)`, or worse). A leading `'` makes it text. `text[:1]` instead of `text[0]` so an empty string doesn't crash.

---

## report.py: three formats, all escaped

- `human_bytes(n)`: divides by 1024 until it fits (`1.2 MB`).
- `_rel(path, plan)`: shows paths relative to the root or dest, whichever contains them.
- `summarise(plan, applied)`: builds one plain dict (the JSON report); Markdown and CSV are both rendered from it, so they can't disagree. `Counter(s.reason.split(" ")[0] ...)` groups skip reasons by their first word.
- `to_markdown(...)`: every file name goes through `safety.md_cell`. Only the first 50 actions are listed; the rest are in `actions.csv`.
- `write_reports(...)`: `csv.writer` handles quoting of commas and quotes; we add formula-neutralising with `safety.csv_cell`. `status = {id(o.action): o ...}` matches outcomes to actions by object identity (actions are frozen dataclasses: two identical moves would compare equal, but they're never the same object).

---

## engine.py: the one public function

```python
def plan_directory(
    root,
    *,
    dest=None,
    rules=None,
    rename=False,
    template=DEFAULT_TEMPLATE,
    dedupe=True,
    include_hidden=False,
    min_dup_size=1,
    exclude=(),
):
    naming.validate_template(template)
    root = Path(root).expanduser().resolve(strict=True)
    dest = Path(dest).expanduser().resolve() if dest else root
    skip = [dest / QUARANTINE_DIR, dest / STATE_DIR, *map(Path, exclude)]
    scanned = scan(root, include_hidden=include_hidden, exclude=skip)
    dups = find_duplicates(scanned.files, min_size=min_dup_size) if dedupe else DuplicateResult()
    return build_plan(scanned, dups, dest=dest, rules=rules or Rules(), rename=rename, template=template)
```
Validate input first (fail fast, before reading the disk). The engine never scans its own quarantine or journal folders. `*map(Path, exclude)` unpacks the converted exclusions into the list.

---

## cli.py: the command line

- `argparse` with sub-commands (`scan`, `organize`/`organise`, `undo`, `generate`). `common(sp)` adds shared flags to each, so they can't drift apart.
- `organize` prints the plan, then **returns before applying unless `--apply`**. That `if not args.apply: ... return 0` is the dry-run guarantee at the user-interface level.
- Every file name printed goes through `safety.display`.
- Exit codes: `0` success, `1` some moves failed (scripts can detect it), `2` bad input. `main()` turns `FileNotFoundError`, `NotADirectoryError` and `ValueError` into a one-line `error:` message instead of a stack trace.
- `__main__.py` lets you run `python -m file_engine`.

---

## generator.py: the test-data factory

- `random.Random(seed)`: a private random generator, so the same seed always gives the same tree, and nothing else in the process affects it.
- `rng.randbytes(n)`: random content, so two files only match if we copied one on purpose. That's what makes the answer key trustworthy.
- `_size()`: 55% small (≤16 KiB, inside the edge window), 38% medium, 7% up to 2 MB.
- Planted duplicates use copy-style names (`(1)`, `Copy of`, ` - Copy`) or, 15% of the time, a completely different name.
- Traps: `same_size` (random bytes, same length), `same_edges` (one byte flipped in the exact middle), `same_head` (last byte flipped). Originals are chosen **without replacement** (`rng.sample`): see postmortem Incident 2.
- Edge cases: empty files, `Report.pdf` vs `report.PDF`, NFC vs NFD `café`, `.tar.gz`, no extension, a 240-character name, hidden files, a symlink and a symlink loop.
- `os.utime(path, (mtime, mtime))` sets access and modified times, spread over ~3 years so date-renames vary.
- Writes `<folder>.answers.json` next to (not inside) the tree, so the engine never scans its own answer key.

---

## Interview drill

**"Why not just `os.walk` and `shutil.move`?"** `os.walk` is fine, but you then need `followlinks=False` *and* an `lstat` per file to spot symlinked files. `shutil.move` overwrites an existing destination on Linux/macOS. Our move uses `os.link` + `unlink`, which is atomic and refuses to overwrite.

**"How do you know two files are duplicates?"** Identical SHA-256 of full contents, found via size → first/last 8 KiB → full hash. 100% precision and recall on 40,280 files including 800 near-duplicate traps.

**"Isn't SHA-256 overkill? Why not MD5?"** MD5 collisions can be made on purpose; someone could craft two different files that look like duplicates, and we'd quarantine one. SHA-256 runs at ~1.2 GB/s here, so the cost difference is negligible.

**"What happens if the power goes out halfway?"** Each move is journaled and fsync'd *before* it happens. Undo reads the journal and checks the disk to see which moves completed. A move is a link then unlink, so the worst case is a file with two names, never zero.

**"What's the time complexity?"** Scanning is O(n) in files. Grouping by size is O(n) with a dict. Hashing is O(bytes read), and the sieves make that much less than total bytes (34% of it on the eval tree). Collision resolution is O(1) average per file with a set.

**"What's the race condition you defended against?"** Time-of-check to time-of-use (TOCTOU): checking `exists()` and then `rename()` leaves a gap. We avoid check-then-act for the dangerous step by using an operation that checks and acts atomically (`os.link`, `open(..., "x")`), and re-validate everything else just before acting.

**"Why is idempotency important here?"** People run tidy-up tools repeatedly, often on a schedule. A non-idempotent rename stacks prefixes (`2026-11-11_2026-11-11_x.jpg`) or shuffles `-2` suffixes every run. The eval checks a second run plans 0 actions on all 20 trees.

**"What would you do differently at scale (millions of files)?"** Cache hashes by `(path, size, mtime)` in SQLite so re-runs only hash new files; hash in a thread pool (hashlib releases the GIL for large buffers); group-commit the journal fsync.
