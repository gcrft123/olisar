"""A stand-in for Gemini in the replay's tests (tests/test_replay*.py): answers each kind of
replay prompt the way a good model would, unless a test says otherwise."""

from __future__ import annotations

import json
import re

from olisar.gemini.client import JsonResult


class FakeGemini:
    """Answers each kind of replay prompt the way a good model would, unless told otherwise."""

    def __init__(self, **o) -> None:
        self.o = o
        self.calls: list[tuple[str, str]] = []
        self.timeouts: list[float | None] = []

    async def generate_json(self, *, contents, system_instruction, chain, model, temperature, max_output_tokens, thinking_budget, source, timeout=None):
        prompt, sys = contents[0], system_instruction
        self.calls.append((sys[:30], prompt))
        self.timeouts.append(timeout)
        if sys.startswith("You read one stretch"):
            data = self.moment(prompt, sys)
        elif sys.startswith("You help find"):
            data = {"motifs": ["candle"], "ordinary": ["chatter"]}
        elif sys.startswith("You're choosing"):
            data = self.o.get("judge") or self.judge(prompt)
        elif sys.startswith("You check one chapter"):
            ids = re.findall(r"^(m\d+) ", prompt, re.M)
            data = {"uses": {i: "yes" for i in ids}, "hurts": self.o.get("hurts", False), "sensitive": False, "reason": ""}
        elif "narrating one chapter" in sys:
            data = self.o.get("writer") or {"intro": "Here, goose means a win nobody saw coming. okapi said it first.",
                                               "spread": "{people} of you have said it since. {count} times so far."}
        elif "narrating a replay" in sys:
            given = dict(line.split(": ", 1) for line in prompt.splitlines())
            data = {k: ("It was mostly about one candle." if "candle" in v else v) if k == "night"
                    else ("the lights went out" if "power" in v else v) if k == "night_title" else v
                    for k, v in given.items()}
        elif sys.startswith("You check a replay"):
            ids = re.findall(r"^(q\d+): (.*)$", prompt, re.M)
            data = {i: ("drop" if "DROPME" in t else "ok") for i, t in ids}
        else:
            raise AssertionError(sys[:60])
        return JsonResult(data=data, text=json.dumps(data), model="gemini-3.5-flash-lite", input_tokens=1000, output_tokens=100)

    def moment(self, prompt: str, sys: str) -> dict:
        ids = re.findall(r"^(m\d+) ", prompt, re.M)
        candle = "candle talk" in prompt
        return {
            "what": "Most of them were about a candle." if candle else "Mostly catching up.",
            "title": "bram's power went out" if candle else "an ordinary evening",
            "kind": "event", "warm": 3, "about_one": False,
            "sensitive": bool(self.o.get("night_sensitive")) and candle,
            "excerpt": ids[:6],
            "why_came": "Someone posted the server on r/cozygames." if "null if the messages" in sys else None,
        }

    @staticmethod
    def judge(prompt: str) -> dict:
        blocks = re.split(r"\n\n(?=c\d\d · )", prompt)
        out = {"candidates": [], "finalists": []}
        for b in blocks:
            head = re.match(r"(c\d\d) · from (\w+) · \"(.*?)\"", b)
            if not head:
                continue
            cid, _, title = head.groups()
            if title.lower() != "goose":
                out["candidates"].append({"id": cid, "disqualified": "means the same thing everywhere"})
                continue
            earliest = re.findall(r"^\s+(m\d+) .*goose", b.split("  later:")[0], re.M)
            later = re.findall(r"^\s+(m\d+) ", b.split("  later:")[1], re.M)
            out["candidates"].append({"id": cid, "scores": {k: 3 for k in ("ours", "shared", "origin", "lasting", "warm", "plain")}})
            out["finalists"].append({"id": cid, "kind": "word", "title": "goose", "meaning": "a win nobody saw coming",
                                     "origin": earliest[0], "trail": later, "why": "Most of the server says it."})
        return out
