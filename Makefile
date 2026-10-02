# Convenience targets. Every target is a thin wrapper over the commands in CLAUDE.md §12.
.PHONY: setup graph-up graph-down pipeline pipeline-core api web test lint check

setup:            ## install Python and web dependencies
	uv sync
	cd web && npm install

graph-up:         ## start the TuringDB server (REST :6666, visualiser :8080)
	docker compose up -d turingdb

graph-down:       ## stop the TuringDB server
	docker compose down

pipeline:         ## full pipeline including the graph layer
	uv run python -m pipeline.run

pipeline-core:    ## pipeline without the graph layer
	uv run python -m pipeline.run --no-graph

api:              ## FastAPI on :8000
	uv run uvicorn api.main:app --reload --port 8000

web:              ## Vite dev server on :5173 (proxies /api to :8000)
	cd web && npm run dev

test:             ## Python test suite
	uv run pytest

lint:             ## ruff + oxlint
	uv run ruff check .
	cd web && npm run lint

check: lint test  ## everything CI runs
	cd web && npm run build
