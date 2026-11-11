# Postmortem

Blameless, in the order things were found. Every number below came from a real run while building v0.1.

---

## Incident 1 (the headline): half the duplicates silently weren't removed

**Severity:** high for the feature, zero data loss.

### What happened

The first full `organize --apply` on a generated 1,014-file folder printed:

```text
Applied: 927 moved, 87 skipped, 0 failed.
  SKIPPED  Downloads/budget 2023 (2).PDF: cannot re-read budget 2023.PDF: No such file or directory
  ... 86 more like it
```

Every one of the 87 was a duplicate copy waiting to be quarantined. The tool had found 180 copies and dealt with only 93 of them (52%).

### How it was found

The skip messages all had the same shape: *"cannot re-read <the keeper>"*. The keeper is the copy we leave in place, so it should have been there. It wasn't, because it had already been **moved** into its category folder by an earlier action in the same run.

A second clue came from re-running the dry run straight after: it wanted to quarantine those same 87 files. A tidy-up tool that has more to do on its second run is not idempotent, and that's a bug in its own right.

### Root cause

The plan listed actions in file-path order. For `budget 2023.PDF` (keeper) and `budget 2023 (2).PDF` (copy), the keeper sorts first, so it was moved to `Documents/2024-…_budget-2023.pdf`. Then the copy's action ran. Before quarantining, the executor re-hashes the keeper **at the path recorded in the plan** to prove an identical file still exists. That path was now empty, so the check refused, correctly.

So the safety check worked: it would rather skip than quarantine a file without proof a twin survives. The *ordering* was wrong.

### Fix

The planner now puts every quarantine action before every organise action (`planner.py`, last three lines), so no keeper moves until all its copies are dealt with. A unit test pins the order (`test_quarantine_runs_before_keeper_moves`) and the accuracy eval now checks, on 20 trees and 6,432 duplicate groups, that every group ends with **exactly one** live copy, and that a second run plans **zero** actions.

| | Before | After |
|---|---|---|
| Copies quarantined on first apply | 93 / 180 (52%) | 180 / 180 |
| Second run planned actions | 87 | 0 |
| Eval: groups with exactly one live copy | (not measured) | 6,432 / 6,432 |
| Eval: idempotent trees | (not measured) | 20 / 20 |

### Lesson

The bug lived *between* two correct pieces: the keeper move was right, the re-check was right, their order wasn't. Unit tests on each piece passed. Only an end-to-end run on realistic data, followed by "run it again", showed it. Idempotency is now a first-class eval metric.

---

## Incident 2: the judge was wrong, twice

The first full accuracy eval reported two failures. Both turned out to be bugs in the **eval**, not the engine.

**"4 of 800 near-duplicate traps wrongly grouped"** (precision 99.98%). On seed 103 two trap files were grouped as duplicates. They were: the generator had built both from the *same* original by flipping the *same* last byte, producing byte-identical files and labelling them different in the answer key. The engine was right. Fix: the generator now picks trap originals without replacement. Precision is 100.0% and traps grouped is 0/800.

**"Undo exact: 0 / 20 trees."** Diffing the before/after manifests showed every file's path, bytes and modified time restored, and 14 *folder* timestamps different. A folder's mtime records when its list of files last changed, so moving files out and back necessarily changes it. The check was stricter than anything possible. Fix: compare folder existence, not folder mtime, after undo. The dry-run check still compares folder mtimes, because a dry run must not touch even those. "Folder timestamps aren't restored" is listed under known failures.

**Lesson:** when a result looks bad, check the measuring stick before changing the thing being measured. Changing the engine to "fix" either of these would have made it worse.

---

## Incident 3: generated data shares the author's blind spots

Categorisation scored **100%** on 40,280 generated files and **32 / 40** on the first run of the hand-written gold set. The misses were `.markdown`, `.mobi`, `.tar.zst`, `.htm`, `.scss`, `.toml`, `Makefile` and `Dockerfile`: things the generator never makes because the same person wrote the generator and the rules.

Fix: a broader extension list and a small table of well-known extensionless names. The gold set now scores 40 / 40, but it has been seen, so that's a regression test, not an accuracy claim. The README shows the first-run score next to it. A fresh, unseen gold set is next step 1.

---

## Incident 4: two attacks were blocked by accident

The attack eval switches each defence off and checks that the attacks it guards against then get through. With **path containment** switched off, the two path-traversal attacks (a rename template that climbs out with `..`, and a rules file with a category of `Docs/../../escaped`) still failed.

Investigation: not a defence, a coincidence. To create `Documents/../../`, the executor first tried `mkdir Documents/..`, which fails because `..` always exists, so the move was skipped. On a second run, when `Documents/` already exists, nothing needs creating and the file would have escaped. (The rules attack also hit an unrelated "category can't start with a dot" check.)

Fix: the attacks now pre-create the folder, as any real second run would. With containment off, 3 / 3 get through; with it on, 0 / 3. The eval also caught that one attack (a tampered undo journal) was listed under the wrong defence.

It happened once more near the end: a later check that refuses templates starting with `.` (so output names can't be hidden files) also refused `../../{name}`, and the containment row quietly dropped to 2 / 3. That check is a real second layer, so it stays; the attack now uses `x/../../../{name}`, which doesn't start with a dot, so the row measures path containment again (3 / 3).

**Lesson:** an ablation that "fails to fail" is a finding, not a pass. Either there's a second defence you should name (the README table lists three such layered cases) or your attack is weaker than a real one.

---

## Incident 5: invisible characters in our own source

The right-to-left override character (U+202E) makes `invoice\u202efdp.exe` *display* as `invoiceexe.pdf`. Tests for it should contain the escape `\u202e`, not the character itself. The CI guard `scripts/check_invisible_chars.py` found **5 literal U+202E characters** across the tests, the attack eval and the guard's own self-test on its first run. They had been converted from escapes while writing the files, and were invisible in every editor view.

It then caught the same thing again in this postmortem and in the cheatsheet while they were being written: the conversion happens at writing time, so a one-off clean-up would not have held. Fix: replaced with escapes. The guard runs in `make lint` and CI, and it tests itself first (`assert bad_chars("ok\u202eevil") == ...`) so it can't silently pass by being broken.

---

## Also found along the way

- **A speed claim built on an unfair comparison.** The first draft of the design doc said the 3 sieves were "only ~1.4× faster" than hashing everything on a warm cache. That compared the whole dry run (scan + dedupe + plan) with hashing alone. Adding a dedupe-only timing to `evals/perf.py` gave 203 ms vs 583 ms: ~2.9×. Caught before publishing because the README table put the two rows side by side and they measured different things.
- **fsync is 70% of apply time** (5,000 moves: ~2.0 s with, ~0.6 s without). Kept for crash safety; see the design doc.
- **The engine's own journal folder was counted as "skipped: hidden"** on re-runs, because the hidden-file check ran before the exclude check. Reordered; the report no longer counts the tool's own files.
