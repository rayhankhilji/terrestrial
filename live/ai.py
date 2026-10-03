"""Runtime AI for the live server (CLAUDE.md §16.3): Jev, TypeSafe's System-One decision model.

Code computes every number; Jev only judges, answering typed questions (noul / choice / score)
about state we prepare. Every call goes through here:
- input-hash cache (the same question about the same state is asked once);
- a per-minute budget (calls beyond it are refused, not queued);
- an append-only log in data/live/ai_calls.jsonl with request hash, model, answers and usage,
  so every AI-derived fact can be traced to the call that produced it.

Disabled cleanly when TYPESAFE_API_KEY is not set: `available` is False and callers fall back to
their deterministic rules, saying so in the UI.

REST contract (docs.typesafe.ai/api): POST https://api.typesafe.ai/v1/systemone, Bearer key,
body {state, model, questions: {id: {type, instructions, criteria?}}} →
{model, answers: {id: {type, noul | choice+probabilities+confidence | score+legend+probabilities+confidence}}, usage}.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any

import httpx

from pipeline.config import LIVE_DIR, optional_key

log = logging.getLogger("terrestrial.live")

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"
BUDGET_PER_MIN = 30
CACHE_SIZE = 2000
CALL_LOG = LIVE_DIR / "ai_calls.jsonl"


class AIUnavailable(RuntimeError):
    """No key, budget exhausted, or the service failed: callers use their deterministic rules."""


@dataclass(frozen=True)
class Answer:
    type: str
    value: Any  # noul: float; choice: str; score: float
    probabilities: dict[str, float] | None
    confidence: float | None
    model: str
    call: str  # request hash, the provenance handle in ai_calls.jsonl


def noul(instructions, criteria: dict | None = None) -> dict:
    q = {"type": "noul", "instructions": instructions}
    if criteria:
        q["criteria"] = criteria
    return q


def choice(instructions, options: dict[str, Any]) -> dict:
    if not 2 <= len(options) <= 255:
        raise ValueError("a Choice needs 2–255 options")
    return {"type": "choice", "instructions": instructions, "criteria": options}


def score(instructions, levels: list) -> dict:
    if not 2 <= len(levels) <= 10:
        raise ValueError("a Score needs 2–10 levels")
    return {"type": "score", "instructions": instructions, "criteria": levels}


def _parse(body: dict, call: str) -> dict[str, Answer]:
    model = body.get("model")
    answers = body.get("answers")
    if not isinstance(model, str) or not isinstance(answers, dict):
        raise AIUnavailable(f"unexpected Jev response shape: {json.dumps(body)[:300]}")
    out = {}
    for qid, a in answers.items():
        kind = a.get("type")
        if kind == "noul":
            out[qid] = Answer(kind, float(a["noul"]), None, None, model, call)
        elif kind == "choice":
            out[qid] = Answer(kind, a["choice"], a["probabilities"], a.get("confidence"), model, call)
        elif kind == "score":
            out[qid] = Answer(kind, float(a["score"]), a["probabilities"], a.get("confidence"), model, call)
        else:
            raise AIUnavailable(f"unknown Jev answer type {kind!r} for {qid}")
    return out


class Jev:
    def __init__(self, key: str | None = None, budget_per_min: int = BUDGET_PER_MIN):
        self.key = key if key is not None else optional_key("TYPESAFE_API_KEY")
        self.budget = budget_per_min
        self.calls: deque[float] = deque()
        self.cache: OrderedDict[str, dict[str, Answer]] = OrderedDict()
        self._http: httpx.AsyncClient | None = None

    @property
    def available(self) -> bool:
        return bool(self.key)

    async def ask(self, state: Any, questions: dict[str, dict]) -> dict[str, Answer]:
        if not self.key:
            raise AIUnavailable("TYPESAFE_API_KEY is not set")
        body = {"state": state, "model": JEV_MODEL, "questions": questions}
        call = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]
        if call in self.cache:
            self.cache.move_to_end(call)
            return self.cache[call]
        now = time.monotonic()
        while self.calls and now - self.calls[0] > 60:
            self.calls.popleft()
        if len(self.calls) >= self.budget:
            raise AIUnavailable(f"Jev budget of {self.budget} calls/min reached")
        self.calls.append(now)
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=10.0)
        try:
            response = await self._http.post(
                JEV_URL, json=body, headers={"Authorization": f"Bearer {self.key}"}
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AIUnavailable(f"Jev call failed: {type(exc).__name__}: {exc}") from exc
        answers = _parse(payload, call)
        self.cache[call] = answers
        if len(self.cache) > CACHE_SIZE:
            self.cache.popitem(last=False)
        self._log(call, body, payload)
        return answers

    def _log(self, call: str, request: dict, response: dict) -> None:
        CALL_LOG.parent.mkdir(parents=True, exist_ok=True)
        with CALL_LOG.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {"at": time.time(), "call": call, "request": request, "response": response}, default=str
                )
                + "\n"
            )


jev = Jev()
