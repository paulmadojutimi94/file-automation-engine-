PY ?= python3
export PYTHONPATH := src

.PHONY: test lint eval check demo clean

test:            ## 68 unit and end-to-end tests
	$(PY) -m pytest -q

lint:
	ruff check src tests evals scripts docs && ruff format --check src tests evals scripts docs
	$(PY) scripts/check_invisible_chars.py

eval:            ## every eval (~3 min), rewrites the README results table
	$(PY) evals/run_all.py

check:           ## README table must match evals/results/
	$(PY) evals/run_all.py --check

demo:            ## generate a messy folder, dry run, apply, re-run, undo
	rm -rf demo && $(PY) -m file_engine generate demo/messy --files 1000 --seed 7
	$(PY) -m file_engine organize demo/messy --rename --report-dir demo/reports
	$(PY) -m file_engine organize demo/messy --rename --report-dir demo/reports --show 0 --apply
	$(PY) -m file_engine organize demo/messy --rename --report-dir demo/reports --show 0
	$(PY) -m file_engine undo $$(ls demo/messy/.file-engine/journal-*.jsonl) --apply

clean:
	rm -rf demo reports .pytest_cache .ruff_cache
