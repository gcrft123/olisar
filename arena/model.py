"""The harness's own model access, split by role and budgeted per backend.

Two roles — ``dialogue`` (emulator chat lines) and ``judge`` (verdicts) — each pointed at
its own backend, because they have opposite requirements and, more importantly, because
leaving both on Gemini makes the harness compete with the thing it is measuring. Free-tier
Gemini is ~10 RPM on the top chat model, shared with the bot under test, so a scenario in
full flow starves Olisar of exactly the quota whose absence it is about to record as a bad
reply.

Budgets are per backend because the constraints are different in kind:

* **Gemini** is rate-and-quota limited and shared with the instance under test, so the
  ceiling is a *call count*.
* **Claude** is billed (or drawn from a subscription's limits), so the ceiling is *dollars*.

Both are hard stops that raise rather than degrade. A harness that quietly stops calling the
judge produces a scorecard that looks complete and is not.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import date
from pathlib import Path

from arena.backends import CLAUDE, GEMINI, GROK, Backend, Completion, build, extract_json
from arena.config import ArenaConfig

log = logging.getLogger("arena.model")

DIALOGUE = "dialogue"
JUDGE = "judge"

# A floor under our own request rate, independent of the bot's throttle. Only Gemini
# needs it — the Claude CLI's own process spawn is slower than this anyway.
_MIN_GAP_SECONDS = 1.2


class BudgetExhausted(RuntimeError):
    """A daily ceiling is spent. Raised, never swallowed — see the module docstring."""


class ModelClient:
    """Role-routed, budgeted access to whatever backends the arena is configured with."""

    def __init__(self, cfg: ArenaConfig) -> None:
        self._cfg = cfg
        self._last_gemini_call = 0.0
        self._lock = asyncio.Lock()
        self._built: dict[str, Backend] = {}
        self._spec = {
            # (backend kind, model, thinking). The judge keeps thinking on — its job is a
            # considered comparison, and the saving from disabling it is trivial at
            # judging volume. Dialogue turns it off: a two-word chat message does not need
            # 800 thinking tokens, and switching it off is worth ~5x on both cost and
            # latency per line.
            DIALOGUE: (cfg.dialogue_backend, cfg.dialogue_model, False),
            JUDGE: (cfg.judge_backend, cfg.judge_model, True),
        }

    def backend(self, role: str) -> Backend:
        """The backend for a role, constructed on first use.

        Lazily, so a dialogue-only operation doesn't fail on the judge's missing
        credentials and vice versa — the roles are independently configured, and a harness
        that demanded both sets of keys to generate one chat line would make the split
        pointless.
        """
        role = role if role in self._spec else DIALOGUE
        if role not in self._built:
            kind, model, thinking = self._spec[role]
            self._built[role] = build(
                kind,
                model,
                gemini_api_key=self._cfg.gemini_api_key,
                claude_binary=self._cfg.claude_binary,
                grok_binary=self._cfg.grok_binary,
                grok_effort=self._cfg.grok_effort,
                thinking=thinking,
                cwd=str(self._cfg.repo_root),
            )
        return self._built[role]

    def describe(self) -> dict:
        """What each role is configured to use. Reads config; constructs nothing, so this
        is safe to call from ``status`` on a half-configured setup."""
        return {
            role: {"backend": kind, "model": model}
            for role, (kind, model, _) in self._spec.items()
        }

    # ── ledger ────────────────────────────────────────────────────────────

    @property
    def _ledger_path(self) -> Path:
        return self._cfg.data_dir / "arena_model_usage.json"

    def _read_ledger(self) -> dict:
        try:
            data = json.loads(self._ledger_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _today(self) -> dict:
        entry = self._read_ledger().get(date.today().isoformat(), {})
        return entry if isinstance(entry, dict) else {}

    def gemini_calls_today(self) -> int:
        return int(self._today().get("gemini_calls", 0))

    def claude_usd_today(self) -> float:
        return float(self._today().get("claude_usd", 0.0))

    def grok_usd_today(self) -> float:
        return float(self._today().get("grok_usd", 0.0))

    def grok_usd_remaining(self) -> float:
        return max(0.0, self._cfg.grok_daily_usd - self.grok_usd_today())

    def gemini_calls_remaining(self) -> int:
        return max(0, self._cfg.daily_model_call_budget - self.gemini_calls_today())

    def claude_usd_remaining(self) -> float:
        return max(0.0, self._cfg.claude_daily_usd - self.claude_usd_today())

    def usage(self) -> dict:
        """What the ceilings look like right now — surfaced by ``arena status``."""
        return {
            "gemini_calls_today": self.gemini_calls_today(),
            "gemini_calls_remaining": self.gemini_calls_remaining(),
            "claude_usd_today": round(self.claude_usd_today(), 4),
            "claude_usd_remaining": round(self.claude_usd_remaining(), 4),
            "grok_usd_today": round(self.grok_usd_today(), 4),
            "grok_usd_remaining": round(self.grok_usd_remaining(), 4),
        }

    def _charge(self, backend: Backend, usd: float) -> None:
        ledger = self._read_ledger()
        today = date.today().isoformat()
        entry = ledger.get(today) or {}
        if backend.name == GEMINI:
            entry["gemini_calls"] = int(entry.get("gemini_calls", 0)) + 1
        else:
            entry[f"{backend.name}_calls"] = int(entry.get(f"{backend.name}_calls", 0)) + 1
            entry[f"{backend.name}_usd"] = round(
                float(entry.get(f"{backend.name}_usd", 0.0)) + usd, 6
            )
        ledger[today] = entry
        for key in sorted(ledger)[:-14]:  # keep a fortnight
            ledger.pop(key, None)
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self._ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")

    def _check_budget(self, backend: Backend) -> None:
        if backend.name == GEMINI and self.gemini_calls_remaining() <= 0:
            raise BudgetExhausted(
                f"the arena has spent its {self._cfg.daily_model_call_budget} Gemini calls for "
                f"today. Raise ARENA_DAILY_CALL_BUDGET, switch a role to the Claude backend, "
                f"or resume tomorrow."
            )
        if backend.name == GROK and self.grok_usd_remaining() <= 0:
            raise BudgetExhausted(
                f"the arena has spent its ${self._cfg.grok_daily_usd:.2f} Grok budget for "
                f"today. Raise ARENA_GROK_DAILY_USD or resume tomorrow."
            )
        if backend.name == CLAUDE and self.claude_usd_remaining() <= 0:
            raise BudgetExhausted(
                f"the arena has spent its ${self._cfg.claude_daily_usd:.2f} Claude budget for "
                f"today (${self.claude_usd_today():.2f} used). Raise ARENA_CLAUDE_DAILY_USD "
                f"or resume tomorrow."
            )

    # ── generation ────────────────────────────────────────────────────────

    async def _call(
        self, role: str, prompt: str, *, system: str, temperature: float,
        max_output_tokens: int, schema: dict | None = None,
    ) -> Completion:
        backend = self.backend(role)
        self._check_budget(backend)

        if backend.name == GEMINI:
            async with self._lock:
                gap = _MIN_GAP_SECONDS - (time.monotonic() - self._last_gemini_call)
                if gap > 0:
                    await asyncio.sleep(gap)
                self._last_gemini_call = time.monotonic()

        result = await backend.complete(
            prompt, system=system, temperature=temperature,
            max_output_tokens=max_output_tokens, schema=schema,
        )
        # Charge even on a failure: a refused, empty or errored response still consumed a
        # request against the quota, and a ledger that only counts successes will happily
        # run a rate-limited loop forever.
        self._charge(backend, result.usd)
        if result.error:
            log.warning("%s (%s/%s) failed: %s", role, backend.name, backend.model, result.error)
        return result

    async def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        role: str = DIALOGUE,
        temperature: float = 1.0,
        max_output_tokens: int = 400,
    ) -> str:
        """One completion, or "" if the backend gave nothing usable."""
        result = await self._call(
            role, prompt, system=system, temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        return result.text

    async def generate_json(
        self,
        prompt: str,
        *,
        system: str = "",
        role: str = JUDGE,
        schema: dict | None = None,
        max_output_tokens: int = 900,
    ) -> dict:
        """A completion parsed as a JSON object, ``{}`` when nothing parses.

        ``{}`` rather than a guess, because every caller treats it as "no verdict". A judge
        that scored 0 on a parse failure would make variants look worse the harder their
        output was to parse, rather than the worse they were.

        With the Claude backend a schema is enforced by the API; with Gemini it falls back
        to asking for JSON and extracting it, which is why the instruction is appended
        either way.
        """
        result = await self._call(
            role,
            prompt,
            system=(system + "\n\nRespond with ONLY a JSON object, no prose, no code fence.").strip(),
            temperature=0.2,
            max_output_tokens=max_output_tokens,
            schema=schema,
        )
        if not result.text:
            return {}
        parsed = extract_json(result.text)
        if not parsed:
            log.warning("%s returned unparseable JSON: %s", role, result.text[:200])
        return parsed
