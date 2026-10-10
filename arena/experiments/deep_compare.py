"""Does Pro earn its price on the deep tier? The same hard prompts, each with an answer that
can be checked, sent to Gemini 3.1 Pro and to 3.8 Flash, both at high thinking.

    uv run python -m arena.experiments.deep_compare --key-var GOOGLE_BILLING_KEY [--max-usd 1.0]

Calls Google directly with the key named in ``.env.arena`` (never printed), not through the
arena instance, so these requests aren't in the instance's usage or its budget: the script
prices each one itself (olisar.gemini.pricing) and stops at ``--max-usd``. Results go to
``arena/runs/_deep/<timestamp>.json``.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from google import genai
from google.genai import types

from arena.config import RUNS_DIR
from arena.experiments.billing_session import _env_value
from olisar.gemini.models import PRO_MODEL
from olisar.gemini.pricing import request_cost
from olisar.gemini.quota import quota_day

OUT_DIR = RUNS_DIR / "_deep"
FLASH = "gemini-3.8-flash"
SYSTEM = (
    "You are Olisar, a member of a Discord server who helps people. Answer in plain text a "
    "Discord message can hold: no headings, short paragraphs. Give the final answer clearly."
)


@dataclass(frozen=True)
class Case:
    name: str
    prompt: str
    expect: str  # a regular expression the right answer matches


CASES = [
    Case("train", "a train leaves at 3pm going 60 mph, another leaves the same station at 4pm going 80 mph the same way. when does the second catch up? show the steps", r"\b7(:00)?\s*(pm|p\.m\.)"),
    Case("aces", "what's the probability of drawing two aces in a row from a standard 52 card deck without replacement? give it as a fraction and a percent", r"1\s*/\s*221|0?\.45\s*%|0?\.452"),
    Case("interest", "how much is $5000 after 10 years at 4% a year compounded monthly? show the formula", r"7,?45[34]\.\d\d|7,?454"),
    Case("pumps", "a pump fills a tank in 6 hours and another in 3 hours, while a drain empties it in 4 hours. with all three open from empty, how long until it's full?", r"\b4 hours|\bfour hours"),
    Case("aliasing", "what does this python print, and why?\n```python\nx = [1, 2, 3]\ny = x\ny += [4]\nx = x + [5]\nprint(y)\n```", r"\[1,\s*2,\s*3,\s*4\](?!,\s*5)"),
    Case("bayes", "a test catches 99% of people who have a condition and wrongly flags 5% of people who don't. 1% of people have it. if i test positive, what's the chance i actually have it?", r"\b1[67](\.\d+)?\s*%|\b0?\.1[67]"),
    Case("elo", "i'm rated 1500 and beat someone rated 1700. with k=32 how many elo points do i gain? show the expected score", r"\b24(\.\d)?\b"),
    Case("default-arg", "why does this keep growing every call and how do i fix it?\n```python\ndef add(item, items=[]):\n    items.append(item)\n    return items\n```", r"(?i)(evaluated|created|defined) (only )?once|default.{0,40}(shared|same list|mutable)"),
]


# Harder: problems models are known to get wrong when they don't think them through.
HARD_CASES = [
    Case("tuesday-boy", "i have two kids. at least one is a boy born on a tuesday. what's the probability both are boys? assume days and sexes are equally likely and independent", r"13\s*/\s*27|0?\.481|48\.1\s*%"),
    Case("remainders", "what's the smallest positive whole number that leaves a remainder of 1 when divided by 2, 3, 4, 5 or 6, and divides evenly by 7?", r"\b301\b"),
    Case("dominoes", "how many ways are there to tile a 2 by 10 board with 1 by 2 dominoes?", r"\b89\b"),
    Case("calendar", "today is saturday, october 10, 2026. what day of the week will october 10, 2030 be?", r"(?i)\bthursday\b"),
    Case("zeros", "how many zeros are at the end of 100 factorial?", r"\b24\b"),
]


@dataclass
class Answer:
    case: str
    model: str
    correct: bool
    seconds: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    text: str
    error: str = ""


def ask(client: genai.Client, model: str, prompt: str) -> Answer:
    started = time.monotonic()
    try:
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                max_output_tokens=8192,
                thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.HIGH),
            ),
        )
    except Exception as exc:  # noqa: BLE001 — record and move on
        return Answer("", model, False, round(time.monotonic() - started, 1), 0, 0, 0.0, "", str(exc)[:300])
    usage = resp.usage_metadata
    inp = (usage.prompt_token_count or 0) if usage else 0
    out = ((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)) if usage else 0
    text = (resp.text or "").strip()
    return Answer(
        "", model, False, round(time.monotonic() - started, 1), inp, out,
        request_cost(model, quota_day(), input_tokens=inp, output_tokens=out), text,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-var", default="GOOGLE_BILLING_KEY")
    parser.add_argument("--max-usd", type=float, default=1.0)
    parser.add_argument("--set", choices=("standard", "hard"), default="standard")
    args = parser.parse_args()
    cases = HARD_CASES if args.set == "hard" else CASES
    client = genai.Client(api_key=_env_value(args.key_var))
    answers: list[Answer] = []
    spent = 0.0
    for case in cases:
        for model in (PRO_MODEL, FLASH):
            if spent >= args.max_usd:
                print(f"stopping: ${spent:.4f} spent, cap ${args.max_usd:.2f}")
                break
            a = ask(client, model, case.prompt)
            a.case = case.name
            a.correct = bool(re.search(case.expect, a.text, re.I))
            spent += a.cost_usd
            answers.append(a)
            print(
                f"{case.name:<12} {model:<24} {'right' if a.correct else 'WRONG'} "
                f"{a.seconds:>5.1f}s out={a.output_tokens:>5} ${a.cost_usd:.4f} {a.error[:60]}"
            )
    for model in (PRO_MODEL, FLASH):
        mine = [a for a in answers if a.model == model]
        if mine:
            print(
                f"\n{model}: {sum(a.correct for a in mine)}/{len(mine)} right, "
                f"median {sorted(a.seconds for a in mine)[len(mine) // 2]:.1f}s, "
                f"${sum(a.cost_usd for a in mine):.4f}"
            )
    print(f"total ${spent:.4f}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    out.write_text(json.dumps([asdict(a) for a in answers], indent=2, ensure_ascii=False))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
