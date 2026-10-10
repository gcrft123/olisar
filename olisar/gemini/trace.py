"""What one reply asked Gemini for: which models answered, and what each answer cost.

The client adds to the trace in effect (``current``) after every request it records, so
whoever set one, the reply pipeline, can say afterwards which model wrote the reply, whether
it fell back, and what the reply cost, without threading a result object through every call.
A request made while no trace is set (background work) isn't kept anywhere but the usage
rollup.

A tool that calls Gemini itself (web search, a catch-up) runs inside the reply, so its
request lands in the reply's trace too, under its own source.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Served:
    model: str
    source: str
    input_tokens: int
    output_tokens: int  # thinking included, as Google bills it
    cost: float  # USD, at Google's paid rates; what it would have cost on a free key
    thinking_tokens: int = 0  # the part of output_tokens that was thinking


@dataclass
class ReplyTrace:
    served: list[Served] = field(default_factory=list)

    def add(self, served: Served) -> None:
        self.served.append(served)

    @property
    def cost(self) -> float:
        return sum(s.cost for s in self.served)

    @property
    def last_model(self) -> str:
        """The model that wrote the reply's last turn, or "" when none answered."""
        return self.served[-1].model if self.served else ""

    def models(self) -> list[str]:
        """Every model that answered, in order, each once."""
        return list(dict.fromkeys(s.model for s in self.served))


current: ContextVar[ReplyTrace | None] = ContextVar("olisar_reply_trace", default=None)
