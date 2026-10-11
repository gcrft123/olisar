"""How a replay asks a model anything: once per question, paced, and on the record.

* Once. Every answer is kept in the run's state under a key ("moment:7", "judge"), so a run
  that stops partway and carries on doesn't pay for the same answer twice.
* Paced. Replies and the replay share the same per-minute windows (the rate limiter has no
  notion of priority), so the replay waits while a model's window is more than half full.
  Members asking Olisar something mid-replay still get the head of the chain.
* On the record. Requests, tokens and an estimate of the cost go on the run, and the calls
  are tagged ``replay`` for the Usage page.

Three kinds of model, by what the step needs: ``lite`` (reading moments, the checks), the
``strong`` head of the chat chain with thinking (PICK.md's judge), and the server's own
``chat`` model for anything said in the persona's voice.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from typing import Awaitable, Callable

from olisar.config import settings
from olisar.gemini.client import get_gemini
from olisar.gemini.models import model_chain, rpm_for
from olisar.gemini.pricing import request_cost
from olisar.gemini.quota import quota_day
from olisar.gemini.rate_limiter import RateLimitExceeded, get_rate_limiter, reply_chain

log = logging.getLogger("olisar.replay")

SOURCE = "replay"
PACE_SHARE = 0.5          # the replay waits while a model's minute is this full
PACE_WAIT_MAX = 90.0      # and gives up waiting (and just asks) after this long
MINUTE_RETRIES = 5        # a chain full for the minute is waited out this many times
MINUTE_WAIT = 60.0


def chain_for(kind: str, guild_model: str | None = None) -> list[str]:
    if kind == "lite":
        return model_chain(settings.gemini_lite_model)
    if kind == "strong":
        return model_chain(settings.gemini_chat_model)
    return reply_chain(guild_model)


async def pace(chain: list[str]) -> None:
    """Wait until the first usable model in ``chain`` has room for replies as well."""
    limiter = get_rate_limiter()
    waited = 0.0
    while waited < PACE_WAIT_MAX:
        head = next((m for m in chain if limiter.state(m) in ("ok", "rpm_full")), None)
        if head is None:
            return   # nothing usable: let the call raise and the run pause
        if limiter.current(head) < max(1, int(rpm_for(head, limiter.paid) * PACE_SHARE)):
            return
        step = max(1.0, min(10.0, limiter.back_in(head) or 2.0))
        await asyncio.sleep(step)
        waited += step


@dataclass
class Spend:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


Answer = Callable[..., Awaitable[dict | None]]


class Asker:
    """Asks on a run's behalf. ``state`` is the run's state dict (answers are kept in
    ``state["calls"]``); ``save`` persists it and the spend after every new answer."""

    def __init__(self, state: dict, *, guild_model: str | None, save: Callable[[Spend], Awaitable[None]], client=None) -> None:
        self.state = state
        self.calls: dict = state.setdefault("calls", {})
        self.guild_model = guild_model
        self.save = save
        self.client = client
        self.spend = Spend()

    def known(self, key: str) -> bool:
        return key in self.calls

    async def ask(
        self, key: str, *, kind: str, system: str, prompt: str,
        temperature: float = 0.2, max_output_tokens: int = 2048, thinking_budget: int | None = 0,
    ) -> dict | None:
        """The model's JSON answer to ``prompt``, or None if it gave none. Raises the
        client's RateLimitExceeded or BudgetSpent for the run to pause on."""
        if self.calls.get(key) is not None:
            return self.calls[key]
        chain = chain_for(kind, self.guild_model)
        client = self.client or get_gemini()
        for attempt in range(MINUTE_RETRIES + 1):
            await pace(chain)
            try:
                result = await client.generate_json(
                    contents=[prompt], system_instruction=system, chain=chain, model=chain[0],
                    temperature=temperature, max_output_tokens=max_output_tokens,
                    thinking_budget=thinking_budget, source=SOURCE,
                )
                break
            except RateLimitExceeded:
                # Out for the day (or the budget's spent): the run pauses on this. Only full
                # for the minute, or resting after a server error: wait and ask again.
                if attempt == MINUTE_RETRIES or all(get_rate_limiter().state(m) == "spent" for m in chain):
                    raise
                await asyncio.sleep(MINUTE_WAIT)
        today: date = quota_day()
        self.spend = Spend(
            requests=1, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            cost_usd=request_cost(result.model, today, input_tokens=result.input_tokens, output_tokens=result.output_tokens),
        )
        # An answer with no JSON in it isn't kept, so trying again asks again.
        if result.data is not None:
            self.calls[key] = result.data
        else:
            log.warning("replay: %s answered %s with no JSON", result.model, key)
        await self.save(self.spend)
        return result.data
