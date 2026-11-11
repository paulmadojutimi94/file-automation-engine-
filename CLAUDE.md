# CLAUDE.md: guide for AI agents working in this repo

## What this is
A stdlib-only Python CLI that scans a folder, finds duplicates, renames and organises files, and writes reports. Dry run by default; `--apply` moves files with an undo journal. **No third-party runtime dependencies. Keep it that way.**

## Run things
```bash
export PYTHONPATH=src
make test        # pytest, < 1 s
make lint        # ruff + invisible-character guard
make demo        # generate → dry run → apply → re-run → undo
make eval        # all evals (~3-4 min), rewrites the README results table
make check       # README table matches evals/results/
```

## Module map (data flows top to bottom)
| File | Job | Writes to disk? |
|---|---|---|
| `models.py` | Dataclasses only | no |
| `scanner.py` | Walk the tree with `lstat`; never follow symlinks | no |
| `hashing.py` | Duplicates: size → edge SHA-256 → full SHA-256 | no |
| `rules.py` | Extension → category folder | no |
| `naming.py` | Slugs, templates, 255-byte names | no |
| `planner.py` | Decide every move; resolve name collisions; containment check | no |
| `engine.py` | `plan_directory()`: the public entry point | no |
| `executor.py` | Journal + no-clobber move. **Only file (with undo.py) that changes disk** | yes |
| `undo.py` | Reverse a journal; dry run unless `apply=True` | yes |
| `safety.py` | Every defence against hostile names/paths | no |
| `report.py` | JSON / Markdown / CSV, all escaped | reports only |
| `cli.py` | argparse front end | via the above |
| `generator.py` | Messy test folders + answer key | yes (its own output dir) |

## Invariants. Do not break these
1. Nothing outside `executor.py` and `undo.py` may modify user files. Planning must stay read-only (the accuracy eval checks file **and folder** mtimes after a dry run).
2. Never overwrite: moves go through `executor.move_no_clobber`. Never use `os.rename`, `os.replace` or `shutil.move` on user files.
3. Never delete user files. Duplicates go to `_duplicates/`.
4. Journal line is written and fsync'd **before** each move.
5. Every computed destination passes `safety.is_safe_component` and `safety.ensure_inside`.
6. Every file name printed or written to a report goes through `safety.display` / `md_cell` / `csv_cell`.
7. Quarantine actions run before organise actions (see docs/POSTMORTEM.md, Incident 1).
8. A second run with the same flags plans zero actions.
9. Defences are called as `module.function` (e.g. `safety.ensure_inside`, `executor.stale_reason`) so `evals/attacks.py` can switch each off. Don't `from x import` them.

## When you change behaviour
- Add a unit test in `tests/`. If it's a defence, add an attack in `evals/attacks.py` **and** an ablation row, and check the attack succeeds with the defence off (an ablation that can't fail proves nothing).
- Run `make eval` and commit `evals/results/*.json` with the README change. CI re-runs accuracy, gold and attacks and fails if the README drifts.
- Don't tune anything on seeds 100-119 (held out) or edit `evals/gold_names.json` labels to make a test pass.
- Don't paste literal invisible characters (U+202E etc.) into source; write escapes. `scripts/check_invisible_chars.py` enforces this.

## Docs to update with behaviour changes
`docs/CHEATSHEET.md` (line-by-line), `docs/DESIGN.md` (if a decision changes), `README.md` known failures.
