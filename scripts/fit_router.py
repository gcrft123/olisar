"""Fit model routing's classifier (olisar/routing/model.json) from labeled examples.

    uv run --with numpy python -m scripts.fit_router            # features only
    GEMINI_API_KEY=... uv run --with numpy python -m scripts.fit_router --embed

Reads ``olisar/routing/training/examples.jsonl`` (``{"text", "tier"}`` per line) and holds
``arena_holdout.jsonl`` out of fitting entirely, as a second check written separately from
the scenarios' own messages.

With ``--embed`` each text is embedded the way recall embeds a message (gemini-embedding-001,
RETRIEVAL_QUERY, 768 dimensions, normalized), cached under ``data/router_embeddings.json``
so a re-fit doesn't ask again. Use a free key: it's a dozen batch requests.

Two logistic models (light enough / needs deep) are fit twice, over the features alone and
over the features plus the similarity to each tier's example centroids. The similarity
features a model trains on come from centroids built without that example (out of fold),
because at runtime no message is ever one of the examples; the centroids shipped are built
from all of them.

Thresholds are set on out-of-fold scores, with precision weighted to a realistic traffic
mix (TRAFFIC below) rather than the examples' own, which have far more deep messages than a
Discord server does. The light threshold keeps at least 97% of what it sends light truly
light (90% for the loose one free-tier pacing may slide to); the deep one keeps 85%.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from olisar.routing.features import FEATURE_NAMES, extract  # noqa: E402

TRAINING = ROOT / "olisar" / "routing" / "training"
OUT = ROOT / "olisar" / "routing" / "model.json"
CACHE = ROOT / "data" / "router_embeddings.json"
TIERS = ("light", "standard", "deep")
NAMES = ("olisar",)
DIMS = 768
FOLDS = 5
SEED = 7
# What a server's replies look like, roughly: the share each tier has of real traffic.
TRAFFIC = {"light": 0.35, "standard": 0.60, "deep": 0.05}
TARGETS = {"light": 0.97, "light_loose": 0.90, "deep": 0.85}
CENTROIDS_PER_TIER = 6


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (TRAINING / name).read_text().splitlines() if line.strip()]


def embed(texts: list[str]) -> np.ndarray:
    from google import genai
    from google.genai import types

    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    key = lambda t: hashlib.sha256(t.encode()).hexdigest()  # noqa: E731
    missing = [t for t in dict.fromkeys(texts) if key(t) not in cache]
    if missing:
        from google.genai import errors

        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        # A free key counts every text in a batch as a request, 100 a minute: a batch of
        # 50 every 31 seconds stays under it. Saved after each batch, so a stop loses none.
        for start in range(0, len(missing), 50):
            batch = missing[start : start + 50]
            for attempt in range(5):
                try:
                    resp = client.models.embed_content(
                        model="gemini-embedding-001",
                        contents=batch,
                        config=types.EmbedContentConfig(
                            task_type="RETRIEVAL_QUERY", output_dimensionality=DIMS
                        ),
                    )
                    break
                except errors.APIError as exc:
                    if getattr(exc, "code", None) != 429 or attempt == 4:
                        raise
                    print("  rate limited; waiting a minute")
                    time.sleep(61)
            for t, e in zip(batch, resp.embeddings):
                v = np.array(e.values, dtype=float)
                cache[key(t)] = (v / (np.linalg.norm(v) or 1.0)).tolist()
            CACHE.write_text(json.dumps(cache))
            print(f"  embedded {min(start + 50, len(missing))}/{len(missing)}")
            if start + 50 < len(missing):
                time.sleep(31)
    return np.array([cache[key(t)] for t in texts])


def spherical_kmeans(x: np.ndarray, k: int, rng: np.random.Generator) -> np.ndarray:
    k = max(1, min(k, len(x)))
    centers = x[rng.choice(len(x), size=k, replace=False)]
    for _ in range(50):
        assign = np.argmax(x @ centers.T, axis=1)
        new = np.array([
            x[assign == j].mean(axis=0) if np.any(assign == j) else centers[j] for j in range(k)
        ])
        new /= np.linalg.norm(new, axis=1, keepdims=True)
        if np.allclose(new, centers):
            break
        centers = new
    return centers


def build_centroids(vecs: np.ndarray, tiers: np.ndarray, seed: int) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    return {
        t: spherical_kmeans(vecs[tiers == t], CENTROIDS_PER_TIER, rng)
        for t in TIERS if np.any(tiers == t)
    }


def similarities(vecs: np.ndarray, centroids: dict[str, np.ndarray]) -> np.ndarray:
    return np.stack([np.max(vecs @ centroids[t].T, axis=1) for t in TIERS], axis=1)


def fit_logistic(x: np.ndarray, y: np.ndarray, sample_w: np.ndarray, l2: float = 1.0):
    """Weighted L2 logistic regression by Newton's method, on standardized inputs, with the
    standardization folded back into the returned weights."""
    mu, sd = x.mean(axis=0), x.std(axis=0)
    sd[sd < 1e-9] = 1.0
    z = (x - mu) / sd
    a = np.hstack([np.ones((len(z), 1)), z])
    w = np.zeros(a.shape[1])
    reg = np.full(a.shape[1], l2)
    reg[0] = 0.0
    for _ in range(100):
        p = 1 / (1 + np.exp(-(a @ w)))
        grad = a.T @ (sample_w * (p - y)) + reg * w
        hess = (a * (sample_w * p * (1 - p))[:, None]).T @ a + np.diag(reg)
        step = np.linalg.solve(hess, grad)
        w -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    weights = w[1:] / sd
    bias = w[0] - float(np.sum(w[1:] * mu / sd))
    return weights, bias


def predict(x: np.ndarray, weights: np.ndarray, bias: float) -> np.ndarray:
    return 1 / (1 + np.exp(-(x @ weights + bias)))


def class_weights(tiers: np.ndarray) -> np.ndarray:
    """Per-example weights that make the examples count as TRAFFIC's mix."""
    w = np.ones(len(tiers))
    for t in TIERS:
        n = np.sum(tiers == t)
        if n:
            w[tiers == t] = TRAFFIC[t] / (n / len(tiers))
    return w


def threshold(scores: np.ndarray, truth: np.ndarray, weights: np.ndarray, target: float,
              eligible: np.ndarray) -> float:
    """The lowest threshold at which what clears it (among ``eligible``) is right at least
    ``target`` of the time, by weight. 1.01 (never) if none is."""
    best = 1.01
    for t in sorted(set(np.round(scores, 4)), reverse=True):
        picked = eligible & (scores >= t)
        total = weights[picked].sum()
        if total <= 0:
            continue
        if weights[picked & truth].sum() / total >= target:
            best = float(t)
        else:
            break
    return best


def features_of(texts: list[str]):
    feats = [extract(t, NAMES) for t in texts]
    x = np.array([f.vector() for f in feats])
    floored = np.array([f.lookup or f.action or f.injection for f in feats])
    no_deep = np.array([f.injection for f in feats])
    return x, floored, no_deep


def decide(p_light, p_deep, floored, no_deep, th):
    """The policy's decision (olisar.routing.policy), minus what needs a live reply."""
    out = []
    for pl, pd, fl, nd in zip(p_light, p_deep, floored, no_deep):
        if pl >= th["light"] and not fl:
            out.append("light")
        elif pd >= th["deep"] and not nd:
            out.append("deep")
        else:
            out.append("standard")
    return np.array(out)


def report(name: str, truth: np.ndarray, pred: np.ndarray, texts: list[str] | None = None) -> dict:
    rank = {t: i for i, t in enumerate(TIERS)}
    down = int(sum(rank[p] < rank[t] for p, t in zip(pred, truth)))
    up = int(sum(rank[p] > rank[t] for p, t in zip(pred, truth)))
    print(f"\n{name}: {len(truth)} messages, routed too low {down}, too high {up}")
    for i, (p, t) in enumerate(zip(pred, truth)):
        # Routed too light, or sent to deep when it wasn't: the two that cost something.
        if texts is not None and (rank[p] < rank[t] and p == "light" or p == "deep" != t):
            print(f"  {t} -> {p}: {texts[i][:100]!r}")
    print("  truth \\ routed   " + "  ".join(f"{t:>8}" for t in TIERS))
    matrix = {}
    for t in TIERS:
        row = [int(np.sum((truth == t) & (pred == p))) for p in TIERS]
        matrix[t] = dict(zip(TIERS, row))
        print(f"  {t:>14}   " + "  ".join(f"{n:>8}" for n in row))
    return {"n": len(truth), "too_low": down, "too_high": up, "matrix": matrix}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--embed", action="store_true", help="use embeddings (needs GEMINI_API_KEY)")
    args = parser.parse_args()

    train = load("examples.jsonl")
    holdout = load("arena_holdout.jsonl")
    texts = [e["text"] for e in train]
    tiers = np.array([e["tier"] for e in train])
    x_lex, floored, no_deep = features_of(texts)
    weights = class_weights(tiers)
    y_light = (tiers == "light").astype(float)
    y_deep = (tiers == "deep").astype(float)

    vecs = embed(texts + [e["text"] for e in holdout]) if args.embed else None
    train_vecs = vecs[: len(texts)] if vecs is not None else None

    # Folds, stratified by tier.
    rng = random.Random(SEED)
    fold = np.zeros(len(texts), dtype=int)
    for t in TIERS:
        idx = [i for i in range(len(texts)) if tiers[i] == t]
        rng.shuffle(idx)
        for n, i in enumerate(idx):
            fold[i] = n % FOLDS

    # Out-of-fold similarities: each example measured against centroids that exclude it.
    sims = np.zeros((len(texts), 3))
    if train_vecs is not None:
        for k in range(FOLDS):
            tr, te = fold != k, fold == k
            cents = build_centroids(train_vecs[tr], tiers[tr], SEED + k)
            sims[te] = similarities(train_vecs[te], cents)
    x_emb = np.hstack([x_lex, sims])

    results = {}
    for kind, x in (("lexical", x_lex), ("embedded", x_emb)):
        if kind == "embedded" and train_vecs is None:
            continue
        oof_light, oof_deep = np.zeros(len(texts)), np.zeros(len(texts))
        for k in range(FOLDS):
            tr, te = fold != k, fold == k
            wl, bl = fit_logistic(x[tr], y_light[tr], weights[tr])
            wd, bd = fit_logistic(x[tr], y_deep[tr], weights[tr])
            oof_light[te] = predict(x[te], wl, bl)
            oof_deep[te] = predict(x[te], wd, bd)
        th = {
            "light": threshold(oof_light, tiers == "light", weights, TARGETS["light"], ~floored),
            "light_loose": threshold(oof_light, tiers == "light", weights, TARGETS["light_loose"], ~floored),
            "deep": threshold(oof_deep, tiers == "deep", weights, TARGETS["deep"], ~no_deep),
        }
        th["light_loose"] = min(th["light_loose"], th["light"])
        print(f"\n== {kind} thresholds: {th}")
        metrics = {"cross_validated": report(f"{kind}, out of fold", tiers, decide(oof_light, oof_deep, floored, no_deep, th), texts)}
        wl, bl = fit_logistic(x, y_light, weights)
        wd, bd = fit_logistic(x, y_deep, weights)
        results[kind] = {"light": (wl, bl), "deep": (wd, bd), "thresholds": th, "metrics": metrics}

    final = results["embedded"] if "embedded" in results else results["lexical"]
    centroids = build_centroids(train_vecs, tiers, SEED) if train_vecs is not None else {}

    # The held-out arena messages, scored the way the runtime will score them.
    h_texts = [e["text"] for e in holdout]
    h_truth = np.array([e["tier"] for e in holdout])
    hx, hfloored, h_no_deep = features_of(h_texts)
    if centroids:
        hx = np.hstack([hx, similarities(vecs[len(texts):], centroids)])
    hl = predict(hx, *final["light"])
    hd = predict(hx, *final["deep"])
    final["metrics"]["arena_holdout"] = report("arena holdout", h_truth, decide(hl, hd, hfloored, h_no_deep, final["thresholds"]), h_texts)

    def logit(pair):
        return {"weights": [round(float(v), 6) for v in pair[0]], "bias": round(float(pair[1]), 6)}

    lexical = results["lexical"]
    model = {
        "version": 1,
        "features": list(FEATURE_NAMES),
        "dims": DIMS if centroids else 0,
        "centroids": {t: [[round(float(v), 5) for v in c] for c in cs] for t, cs in centroids.items()},
        "embedded": {k: logit(final[k]) for k in ("light", "deep")},
        "lexical": {k: logit(lexical[k]) for k in ("light", "deep")},
        # The embedded thresholds when there are centroids; the runtime falls back to the
        # lexical weights without an embedding, and those have thresholds of their own.
        "thresholds": final["thresholds"],
        "lexical_thresholds": lexical["thresholds"],
        "traffic": TRAFFIC,
        "metrics": {k: v["metrics"] for k, v in results.items()},
        "examples": {t: int(np.sum(tiers == t)) for t in TIERS},
    }
    OUT.write_text(json.dumps(model, separators=(",", ":")) + "\n")
    print(f"\nwrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
