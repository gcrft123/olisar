"""How likely a message is to need the light or the deep tier.

Two logistic models, one for "light is enough" and one for "this needs deep", over the
message's features (olisar.routing.features) and, when it's at hand, how close the
message's embedding sits to labeled examples of each tier. The embedding is the one recall
already made for the reply (olisar.gemini.embeddings.cached_query_vector), so this costs no
request. Without it, a second pair of weights fit on the features alone stands in.

Weights, example centroids and thresholds come from ``model.json`` beside this file,
written by ``scripts/fit_router.py`` from ``training/examples.jsonl``. Pure Python: the
dot products are a few thousand multiplications a message, and the desktop bundle and the
free Oracle box shouldn't carry numpy for that.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path

from olisar.routing.features import FEATURE_NAMES, Features

log = logging.getLogger("olisar.routing")

MODEL_PATH = Path(__file__).with_name("model.json")
TIERS = ("light", "standard", "deep")


@dataclass(frozen=True)
class Scores:
    light: float  # probability the light tier is enough
    deep: float  # probability the message needs the deep tier
    embedded: bool  # whether the embedding took part


@dataclass(frozen=True)
class _Logit:
    weights: tuple[float, ...]
    bias: float

    def __call__(self, x: list[float]) -> float:
        z = self.bias + sum(w * v for w, v in zip(self.weights, x))
        if z < -40:
            return 0.0
        return 1.0 / (1.0 + math.exp(-z))


@dataclass(frozen=True)
class Model:
    dims: int
    centroids: dict[str, tuple[tuple[float, ...], ...]]
    embedded: dict[str, _Logit]  # "light" / "deep", over features + similarities
    lexical: dict[str, _Logit]  # the same, over features alone
    thresholds: dict[str, float]  # for the embedded scores: light, light_loose, deep
    lexical_thresholds: dict[str, float]  # for the features-only scores

    def thresholds_for(self, scores: "Scores") -> dict[str, float]:
        return self.thresholds if scores.embedded else self.lexical_thresholds

    def similarities(self, vector: list[float]) -> list[float]:
        """The highest cosine similarity to each tier's centroids, in TIERS order. Both
        sides are unit vectors, so it's a dot product."""
        out = []
        for tier in TIERS:
            best = -1.0
            for c in self.centroids.get(tier, ()):
                best = max(best, sum(a * b for a, b in zip(c, vector)))
            out.append(best)
        return out

    def score(self, features: Features, vector: list[float] | None) -> Scores:
        x = features.vector()
        if vector and len(vector) == self.dims and self.centroids:
            x = x + self.similarities(vector)
            return Scores(self.embedded["light"](x), self.embedded["deep"](x), True)
        return Scores(self.lexical["light"](x), self.lexical["deep"](x), False)


def _logit(raw: dict) -> _Logit:
    return _Logit(tuple(float(w) for w in raw["weights"]), float(raw["bias"]))


def _parse(raw: dict) -> Model:
    if tuple(raw.get("features", ())) != FEATURE_NAMES:
        raise ValueError("model.json was fit on a different feature set; run scripts/fit_router.py")
    centroids = {
        tier: tuple(tuple(float(v) for v in c) for c in raw.get("centroids", {}).get(tier, ()))
        for tier in TIERS
    }
    return Model(
        dims=int(raw.get("dims", 0)),
        centroids=centroids,
        embedded={k: _logit(raw["embedded"][k]) for k in ("light", "deep")},
        lexical={k: _logit(raw["lexical"][k]) for k in ("light", "deep")},
        thresholds={k: float(v) for k, v in raw["thresholds"].items()},
        lexical_thresholds={
            k: float(v) for k, v in raw.get("lexical_thresholds", raw["thresholds"]).items()
        },
    )


_model: Model | None = None
_failed = False


def model() -> Model | None:
    """The fitted model, loaded once. None if it's missing or unreadable, which turns the
    router's scores off: every message then goes to the standard tier unless a rule says
    otherwise."""
    global _model, _failed
    if _model is None and not _failed:
        try:
            _model = _parse(json.loads(MODEL_PATH.read_text()))
        except Exception:
            _failed = True
            log.exception("model routing's classifier didn't load; routing every reply standard")
    return _model


def score(features: Features, vector: list[float] | None = None) -> Scores | None:
    m = model()
    return m.score(features, vector) if m is not None else None
