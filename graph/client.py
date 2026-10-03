"""Minimal TuringDB client over its HTTP API (CLAUDE.md §15.1).

Mirrors the official SDK's `json` backend: every call is `POST {host}/query` with the
Cypher text as the body and `graph`, `change` (hex id) and `commit` as query parameters.
Responses are columnar: `{"header": {"column_names", "column_types"}, "data": [chunk...]}`
where each chunk is a list of columns; errors come back as `{"error", "error_details"}`.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import httpx

from pipeline.config import TURINGDB_URL


class TuringDBError(RuntimeError):
    pass


def literal(value: Any) -> str:
    """Render a Python value as a Cypher literal. Raises on unsupported types."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"cannot store non-finite float {value!r} in TuringDB")
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ")
        return f"'{escaped}'"
    raise TypeError(f"unsupported Cypher literal type {type(value).__name__}: {value!r}")


def props(values: Mapping[str, Any]) -> str:
    """Render a property map, dropping None values (TuringDB has no null literal)."""
    items = []
    for key, value in values.items():
        if value is None or (isinstance(value, float) and math.isnan(value)):
            continue
        if not key.isidentifier():
            raise ValueError(f"invalid property name {key!r}")
        items.append(f"{key}: {literal(value)}")
    return "{" + ", ".join(items) + "}"


class TuringDB:
    def __init__(self, host: str = TURINGDB_URL, graph: str = "default", timeout: float = 120.0):
        self.host = host.rstrip("/")
        self._http = httpx.Client(timeout=timeout)
        self._params: dict[str, str] = {"graph": graph}

    # --- transport -----------------------------------------------------------------------

    def raw(self, cypher: str, **params: str) -> dict:
        merged = {**self._params, **params}
        try:
            response = self._http.post(
                f"{self.host}/query",
                content=cypher,
                params=merged,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
        except httpx.ConnectError as exc:
            raise TuringDBError(
                f"TuringDB is not reachable at {self.host}. Start it with "
                "`docker compose up -d turingdb` (or `turingdb -demon`)."
            ) from exc
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise TuringDBError(f"unexpected TuringDB response: {body!r:.200}")
        if body.get("error"):
            details = body.get("error_details")
            raise TuringDBError(f"{body['error']}: {details}\nquery: {cypher[:500]}")
        return body

    def query(self, cypher: str, **params: str) -> list[dict[str, Any]]:
        """Run Cypher and return rows as dicts keyed by column name."""
        body = self.raw(cypher, **params)
        header = body.get("header") or {}
        names: list[str] = header.get("column_names") or []
        rows: list[dict[str, Any]] = []
        for chunk in body.get("data") or []:
            if len(chunk) != len(names):
                raise TuringDBError(f"column count mismatch: {len(chunk)} vs {names}")
            rows.extend(dict(zip(names, values, strict=True)) for values in zip(*chunk, strict=True))
        return rows

    # --- graphs --------------------------------------------------------------------------

    @property
    def graph(self) -> str:
        return self._params["graph"]

    def use(self, graph: str) -> None:
        self._params["graph"] = graph

    def available_graphs(self) -> dict[str, bool]:
        rows = self.query("LIST AVAILABLE GRAPHS", graph="default")
        return {r["graphName"]: bool(r["isLoaded"]) for r in rows}

    def ensure_loaded(self, graph: str) -> None:
        """Load an on-disk graph if the server has not loaded it yet (e.g. after restart)."""
        graphs = self.available_graphs()
        if graph not in graphs:
            raise TuringDBError(f"graph {graph!r} does not exist on the server; run the pipeline graph stage")
        if not graphs[graph]:
            self.query(f"LOAD GRAPH {graph}", graph="default")

    # --- versioning ----------------------------------------------------------------------

    @contextmanager
    def change(self) -> Iterator[None]:
        """Open a change; submit it on success, delete it on error."""
        if "change" in self._params:
            raise TuringDBError("a change is already open on this client")
        rows = self.query("CHANGE NEW")
        self._params["change"] = f"{int(rows[0]['changeID']):x}"
        try:
            yield
            self.query("CHANGE SUBMIT")
        except BaseException:
            self.query("CHANGE DELETE")
            raise
        finally:
            del self._params["change"]

    def commit(self) -> None:
        """Persist intermediate state inside an open change (needed between node and edge CREATEs)."""
        if "change" not in self._params:
            raise TuringDBError("COMMIT outside a change")
        self.query("COMMIT")

    def history(self) -> list[str]:
        """Commit hashes, newest first (HEAD first)."""
        return [r["commit"].split("(")[0] for r in self.query("CALL db.history()")]

    def head(self) -> str:
        return self.history()[0]

    @contextmanager
    def at_commit(self, commit: str) -> Iterator[None]:
        """Time-travel: run queries against a past commit."""
        if "change" in self._params:
            raise TuringDBError("cannot time-travel while a change is open")
        self.query(f"LOAD COMMIT {literal(commit)}")
        previous = self._params.get("commit")
        self._params["commit"] = commit
        try:
            yield
        finally:
            if previous is None:
                del self._params["commit"]
            else:
                self._params["commit"] = previous

    def close(self) -> None:
        self._http.close()
