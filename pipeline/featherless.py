"""Featherless.ai client (OpenAI-compatible chat completions over httpx), CLAUDE.md §15.4/§16.3.

Used for prose only (vessel briefs in the pipeline, SITREPs in the live server). The prompt
contains numbered facts ("F1", "F2", …); the model must cite them in square brackets, and
`validate` rejects any text that cites an id we did not give it, or cites nothing at all. Reasoning
models may emit a <think> block first; it is stripped and never shown.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import httpx

from pipeline.config import FEATHERLESS_BASE_URL, featherless_key, featherless_model

CITATION = re.compile(r"\[(F\d+(?:\s*,\s*F\d+)*)\]")
THINK = re.compile(r"<think>.*?</think>", re.S)
SYSTEM = (
    "You are a careful military intelligence analyst writing for Ukrainian defenders. You write only "
    "from the numbered facts you are given. Every sentence must cite the facts it uses in square "
    "brackets, e.g. [F3] or [F3, F7]. Never invent numbers, places, units or times. Use probabilistic "
    "language ('likely', 'consistent with', 'reported') and never 'confirmed'. If the facts do not "
    "support a statement, leave it out."
)


class FeatherlessError(RuntimeError):
    pass


@dataclass(frozen=True)
class Prose:
    text: str
    cited: list[str]
    model: str
    at: float


def facts_block(facts: dict[str, str]) -> str:
    return "\n".join(f"{fid}: {text}" for fid, text in facts.items())


def validate(text: str, facts: dict[str, str], min_citations: int = 2) -> list[str]:
    cited = [c.strip() for group in CITATION.findall(text) for c in group.split(",")]
    unknown = sorted({c for c in cited if c not in facts})
    if unknown:
        raise FeatherlessError(f"cites facts that do not exist: {', '.join(unknown)}")
    if len(set(cited)) < min_citations:
        raise FeatherlessError(f"cites {len(set(cited))} facts; at least {min_citations} required")
    return sorted(set(cited), key=lambda c: int(c[1:]))


def complete(task: str, facts: dict[str, str], max_tokens: int = 700, timeout: float = 90.0) -> Prose:
    key = featherless_key()
    if not key:
        raise FeatherlessError("FEATHERLESS_API_KEY is not set")
    model = featherless_model()
    body = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"{task}\n\nFACTS\n{facts_block(facts)}"},
        ],
    }
    try:
        r = httpx.post(
            f"{FEATHERLESS_BASE_URL}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )
        r.raise_for_status()
        payload = r.json()
        text = payload["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        raise FeatherlessError(f"Featherless call failed: {type(exc).__name__}: {exc}") from exc
    text = THINK.sub("", text or "").strip()
    return Prose(text, validate(text, facts), payload.get("model", model), time.time())
