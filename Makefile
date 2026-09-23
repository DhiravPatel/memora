.PHONY: install migrate api worker web test test-e2e lint format check seed

install:
	python -m venv .venv
	.venv/bin/pip install -e ".[dev]"
	.venv/bin/pip install -e sdk/python
	cd apps/web && npm install
	cd sdk/typescript && npm install

migrate:
	.venv/bin/alembic upgrade head

api:
	.venv/bin/uvicorn app.main:app --reload --port 8000

worker:
	.venv/bin/python apps/worker/main.py

web:
	cd apps/web && npm run dev

test:
	.venv/bin/pytest

test-e2e:
	./scripts/setup_test_db.sh memory_test
	TEST_DATABASE_URL=postgresql+asyncpg://localhost/memory_test .venv/bin/pytest tests/e2e -q

lint:
	.venv/bin/ruff check packages apps tests scripts sdk/python
	cd apps/web && npm run typecheck
	cd sdk/typescript && npm run typecheck

format:
	.venv/bin/ruff check --fix packages apps tests scripts sdk/python
	.venv/bin/ruff format packages apps tests scripts sdk/python

check:
	.venv/bin/python scripts/check_config.py

seed:
	.venv/bin/python scripts/seed_demo.py
