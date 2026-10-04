"""Publish Terrestrial's trained models to the Hugging Face Hub as open models.

    uv run python -m predict.publish                 # all latest models, private repos
    uv run python -m predict.publish --public        # …made public
    uv run python -m predict.publish --dry-run       # write the model cards locally only

Needs HF_TOKEN (a write token from huggingface.co/settings/tokens) in .env. Each model becomes
one repo `<user>/terrestrial-<model>` holding the weights (`model.joblib`, a scikit-learn
pipeline: load only from sources you trust, as with any pickle), the full model card JSON, and
a README model card generated from it: what it predicts, the data, the time split, the scores
against the baselines (including when it does not beat them), and its limitations.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from pipeline.config import MODELS_DIR, optional_key
from pipeline.io import read_json

log = logging.getLogger("terrestrial")

REPO = "https://github.com/rayhankhilji/terrestrial"
MODELS = {
    "strike-new-alert": MODELS_DIR / "strike" / "new",
    "strike-active-alert": MODELS_DIR / "strike" / "active",
    "flight-destination": MODELS_DIR / "flight",
}


def latest(folder: Path) -> Path:
    return folder / read_json(folder / "latest.json")["version"]


def _scores_table(card: dict) -> str:
    scores = card["test_scores"]
    first = next(iter(scores.values()))
    metrics = [m for m in first if isinstance(first[m], (int, float))]
    head = "| | " + " | ".join(metrics) + " |\n|---|" + "---|" * len(metrics) + "\n"
    return head + "".join(
        f"| {name} | " + " | ".join(f"{s[m]:.3f}" for m in metrics) + " |\n" for name, s in scores.items()
    )


def readme(name: str, card: dict) -> str:
    verdict = (
        "**Beats every baseline on the held-out test period.**"
        if card.get("beats_baselines")
        else "**Does not beat every baseline on the held-out test period; use with caution.**"
    )
    limitations = "\n".join(f"- {x}" for x in card.get("limitations", []))
    data = "\n".join(f"- **{k}**: {v}" for k, v in card.get("data", {}).items())
    split = "\n".join(f"- {k}: {v}" for k, v in card.get("split", {}).items())
    features = ", ".join(f"`{f}`" for f in card.get("features", []))
    return f"""---
license: mit
library_name: sklearn
tags:
- tabular-classification
- ukraine
- osint
- defence
- forecasting
---

# Terrestrial · {card["name"]}

{card["target"]}

Part of [Terrestrial]({REPO}), an open-source predictive defence picture built for the European
Defence Tech Hackathon (London, October 2026) in support of Ukraine. Version `{card["version"]}`.

{verdict}

## Test scores (time split, never seen in training)

{_scores_table(card)}
Unit: {card.get("unit", "")}

## Data

{data}

## Split

{split}

## Model

{card.get("algorithm", "")}

Features: {features}

## Limitations

{limitations}

## Use

```python
import joblib
blob = joblib.load("model.joblib")   # {{"model": …, "features": [...], …}}; pickle: trusted sources only
```

The live inference code (feature building identical to training) is in
[`predict/`]({REPO}/tree/main/predict). Outputs are model estimates, not certainties.
"""


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--public", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--only", choices=sorted(MODELS), action="append")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    token = optional_key("HF_TOKEN")
    if not token and not args.dry_run:
        raise SystemExit(
            "HF_TOKEN is not set: create a write token at https://huggingface.co/settings/tokens and add it to .env"
        )
    api = user = None
    if not args.dry_run:
        from huggingface_hub import HfApi

        api = HfApi(token=token)
        user = api.whoami()["name"]
    for name in args.only or sorted(MODELS):
        folder = MODELS[name]
        if not (folder / "latest.json").exists():
            log.warning("%s: no trained model, skipped", name)
            continue
        version = latest(folder)
        card = read_json(version / "model_card.json")
        (version / "README.md").write_text(readme(name, card), encoding="utf-8")
        if args.dry_run:
            log.info("%s: model card written to %s", name, version / "README.md")
            continue
        repo = f"{user}/terrestrial-{name}"
        api.create_repo(repo, private=not args.public, exist_ok=True)
        api.upload_folder(
            repo_id=repo, folder_path=str(version), commit_message=f"Terrestrial {name} {card['version']}"
        )
        log.info(
            "%s: published https://huggingface.co/%s (%s)", name, repo, "public" if args.public else "private"
        )
        print(json.dumps({"model": name, "repo": repo, "version": card["version"]}))


if __name__ == "__main__":
    main()
