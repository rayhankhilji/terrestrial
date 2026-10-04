# Convenience targets. Every target is a thin wrapper over the commands in CLAUDE.md §12.
.PHONY: capture deploy demo-cut demo publish setup graph-up graph-down pipeline pipeline-core api live web test test-graph lint check

setup:            ## install Python and web dependencies
	uv sync
	cd web && npm install

graph-up:         ## start the TuringDB server (REST :6666, visualiser :8080)
	docker compose up -d turingdb

graph-down:       ## stop the TuringDB server
	docker compose down

history:  ## fetch and normalise 2022→now history for the predictive layer
	uv run python -m history.run

pipeline:         ## full pipeline including the graph layer
	uv run python -m pipeline.run

pipeline-core:    ## pipeline without the graph layer
	uv run python -m pipeline.run --no-graph

api:              ## FastAPI on :8000
	uv run uvicorn api.main:app --reload --port 8000

demo-cut:         ## cut data/demo/demo.jsonl from a recording: make demo-cut DAY=20261003
	uv run python -m live.demo --day $(DAY)

capture:          ## record the running live server for the hosted build: make capture MIN=12
	uv run python -m live.capture --minutes $(or $(MIN),12) --out web/public/rec

deploy:           ## build the recorded app and deploy it to Vercel (needs make capture first)
	web/scripts/deploy-hosted.sh

demo:             ## replay data/demo/demo.jsonl at 10× (no network but basemap tiles); run make web alongside
	LIVE_REPLAY=data/demo/demo.jsonl LIVE_REPLAY_SPEED=10 uv run uvicorn live.server:app --port 8001

publish:          ## publish the latest trained models to the Hugging Face Hub (needs HF_TOKEN)
	uv run python -m predict.publish

live:             ## live service on :8001 (ADS-B, AIS, FIRMS, GDELT, weather → WebSocket)
	uv run uvicorn live.server:app --port 8001

web:              ## Vite dev server on :5173 (proxies /api to :8000)
	cd web && npm run dev

test:             ## Python test suite (excludes tests that need the graph server)
	uv run pytest

test-graph:       ## TuringDB integration tests (needs make graph-up)
	uv run pytest -m graph

lint:             ## ruff + oxlint
	uv run ruff check .
	cd web && npm run lint

check: lint test  ## everything CI runs
	cd web && npm run build
