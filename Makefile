.PHONY: install check test run run-live worker soak record backtest docker

VENV := .venv
PY := $(VENV)/bin/python
ifeq ($(OS),Windows_NT)
	PY := $(VENV)/Scripts/python.exe
endif

install:
	python -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements-dev.lock
	$(PY) -m pip install -e . --no-deps

check:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .
	$(PY) -m mypy app
	$(PY) -m pytest

test:
	$(PY) -m pytest

run:
	NANSEN_MODE=replay $(PY) -m uvicorn app.main:app --reload

run-live:
	NANSEN_MODE=live $(PY) -m uvicorn app.main:app --reload

worker:
	$(PY) -m app.worker

soak:
	$(PY) -m pytest tests/soak --soak -q

record:
	$(PY) scripts/record_fixtures.py

backtest:
	$(PY) -m backtest.run

docker:
	docker build -t lp-radar .
