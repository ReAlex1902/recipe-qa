.PHONY: test lint ingest ingest-pinned embed schema dev eval docker

BASE_URL ?= http://localhost:8000

test: lint
	cd backend && uv run pytest
	cd frontend && npm run typecheck

lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy

ingest:
	cd backend && uv run python -m recipe_qa.ingest

ingest-pinned:
	cd backend && uv run python -m recipe_qa.ingest --pinned

embed:
	cd backend && uv run python -m recipe_qa.embeddings_file

schema:
	cd backend && uv run python -m recipe_qa.export_schema

# API + built UI on :8000
dev:
	cd frontend && npm run build
	cd backend && uv run uvicorn recipe_qa.main:app --reload --port 8000

eval:
	uv run --project backend python evals/run_evals.py --base-url $(BASE_URL)

docker:
	docker compose up --build
