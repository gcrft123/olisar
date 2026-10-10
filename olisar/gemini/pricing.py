"""What Gemini charges, so the Usage page can show money.

On a key with billing on, Google charges for everything the project uses, so the page shows
what's been spent. On a free key the same figures say what the day would have cost, which
is the honest answer to "should I turn billing on?".

Prices are Google's standard paid-tier rates in USD per million tokens, from
https://ai.google.dev/gemini-api/docs/pricing as read on 2026-10-09. Output includes
thinking tokens, which Google bills at the output rate, and a grounded request's search
results count as input. A price that changes on a known date lists both, oldest first.
Check the page and update this table when the chain changes.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Protocol

from olisar.gemini.models import GEMINI_IMAGE_MODEL


@dataclass(frozen=True)
class Price:
    input: float  # USD per 1M input tokens
    output: float  # USD per 1M output tokens, thinking included
    image: float = 0.0  # USD per generated image


# Gemini 3.6 and 3.8 Flash. Google doubles both on 2027-01-01.
_FLASH = ((date.min, Price(0.75, 3.75)), (date(2027, 1, 1), Price(1.50, 7.50)))

_PRICES: dict[str, tuple[tuple[date, Price], ...]] = {
    "gemini-3.8-flash": _FLASH,
    "gemini-3.6-flash": _FLASH,
    # An alias for the newest Flash, whatever Google points it at.
    "gemini-flash-latest": _FLASH,
    "gemini-2.5-flash": ((date.min, Price(0.30, 2.50)),),
    "gemini-3.5-flash-lite": ((date.min, Price(0.30, 2.50)),),
    # An alias for the newest Flash-Lite (3.5 Flash-Lite as of this table).
    "gemini-flash-lite-latest": ((date.min, Price(0.30, 2.50)),),
    "gemini-3.1-flash-lite": ((date.min, Price(0.25, 1.50)),),
    "gemini-2.5-flash-lite": ((date.min, Price(0.10, 0.40)),),
    # Retired from the chain (models.RETIRED), kept so the month they were used in still
    # adds up. 3.5 Flash isn't on Google's page any more; priced as the Flash replacing it.
    "gemini-3.5-flash": _FLASH,
    "gemini-3-flash-preview": ((date.min, Price(0.50, 3.00)),),
    # Not on Google's page any more; its last published price.
    "gemini-embedding-001": ((date.min, Price(0.15, 0.0)),),
    # A 1K image is 1,120 output tokens at $30 per million.
    GEMINI_IMAGE_MODEL: ((date.min, Price(0.25, 0.0, image=0.0336)),),
}

# Web search. Gemini 3 and newer share 5,000 free searches a month, then $14 per 1,000. The
# Gemini 2.5 models share 1,500 free a day on a billed key, then $35 per 1,000.
SEARCH_FREE_PER_MONTH = 5000
SEARCH_PRICE = 14 / 1000
SEARCH_25_FREE_PER_DAY = 1500
SEARCH_25_PRICE = 35 / 1000


def price_for(model: str, day: date) -> Price:
    """What ``model`` cost on ``day``. A model the table doesn't know is priced as the newest
    Flash, so an unknown cost is overstated rather than shown as free."""
    current = _FLASH[0][1]
    for since, price in _PRICES.get(model, _FLASH):
        if day >= since:
            current = price
    return current


def is_gen25(model: str) -> bool:
    """Whether web search on ``model`` is billed at the Gemini 2.5 rates."""
    return "2.5" in model


class UsageRow(Protocol):
    day: date
    model: str
    request_count: int
    token_count: int
    input_tokens: int
    output_tokens: int
    grounding_count: int


def tokens_cost(row: UsageRow) -> float:
    """What a day's requests to one model cost, searches aside. Tokens counted before input
    and output were kept apart are priced as input, so those days are understated a little
    rather than guessed at."""
    price = price_for(row.model, row.day)
    split = (row.input_tokens or 0) + (row.output_tokens or 0)
    unsplit = max(0, (row.token_count or 0) - split)
    cost = ((row.input_tokens or 0) + unsplit) * price.input / 1e6
    cost += (row.output_tokens or 0) * price.output / 1e6
    cost += (row.request_count or 0) * price.image
    return cost


def daily_costs(rows: Iterable[UsageRow]) -> dict[date, float]:
    """Cost per day, searches included. The monthly search allowance is counted from the
    first day of each month in ``rows``, so pass whole months for a month's figures to be
    right (earlier days can be left out of what's shown, not out of what's passed)."""
    costs: dict[date, float] = defaultdict(float)
    search_gen3: dict[tuple[int, int], dict[date, int]] = defaultdict(lambda: defaultdict(int))
    search_gen25: dict[date, int] = defaultdict(int)
    for row in rows:
        costs[row.day] += tokens_cost(row)
        if row.grounding_count:
            if is_gen25(row.model):
                search_gen25[row.day] += row.grounding_count
            else:
                search_gen3[(row.day.year, row.day.month)][row.day] += row.grounding_count
    for day, n in search_gen25.items():
        costs[day] += max(0, n - SEARCH_25_FREE_PER_DAY) * SEARCH_25_PRICE
    for days in search_gen3.values():
        used = 0
        for day in sorted(days):
            before, used = used, used + days[day]
            billed = max(0, used - SEARCH_FREE_PER_MONTH) - max(0, before - SEARCH_FREE_PER_MONTH)
            costs[day] += billed * SEARCH_PRICE
    return dict(costs)


def request_cost(
    model: str, day: date, *, input_tokens: int = 0, output_tokens: int = 0, images: int = 0
) -> float:
    """What one request cost, searches aside."""
    price = price_for(model, day)
    return input_tokens * price.input / 1e6 + output_tokens * price.output / 1e6 + images * price.image
