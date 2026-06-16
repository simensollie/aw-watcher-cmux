PY ?= .venv/bin/python

.PHONY: help venv install test verify run clean

help:
	@echo "make venv     - create .venv"
	@echo "make install  - editable install with dev deps into .venv"
	@echo "make test     - run the unit test suite"
	@echo "make verify   - end-to-end check against an isolated aw test server"
	@echo "                (must be run from inside a cmux surface)"
	@echo "make run      - run the watcher against your real aw-server"

venv:
	python3 -m venv .venv

install: venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -e ".[dev]"

test:
	$(PY) -m pytest -q

verify:
	PY=$(PY) scripts/verify.sh

run:
	$(PY) -m aw_watcher_cmux --verbose

clean:
	rm -rf .pytest_cache **/__pycache__ *.egg-info build dist
