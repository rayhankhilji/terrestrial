"""Historical data lake for the predictive layer (CLAUDE.md §16.4).

Each module downloads one open dataset into data/raw/history/<source>/ (cache first) and
normalises it to data/history/<table>.parquet. Run all of them with
`uv run python -m history.run` (flags: --refresh, --only <name>).
"""
