"""How many replies one member, or one server, can have from Olisar.

Every reply is a handful of model calls (up to six tool rounds, two continuations and a
forced answer) against a quota the whole install shares, and nothing limited how many one
person could ask for: thirty @mentions got thirty full replies, and one member could spend
the day's quota for every server the bot is in.

So each reply is taken from two token buckets, one for the member and one for the server
(DMs share one, under guild 0). A bucket holds a burst and refills at a steady rate. The
sizes are set so a conversation never notices them: a member gets eight replies back to
back and then one every 15 seconds, and a server thirty and then one every 4 seconds.
Someone flooding the bot runs dry within a few messages.

Kept in memory, like the proactive cooldowns. A restart refills everyone, which is harmless.
"""

from __future__ import annotations

import time

MEMBER_BURST = 8
MEMBER_PER_MINUTE = 4.0
SERVER_BURST = 30
SERVER_PER_MINUTE = 15.0

# Past this many tracked keys, full buckets are dropped (an unknown key starts full anyway).
_PRUNE_AT = 5000


class TokenBucket:
    """Per-key buckets of ``burst`` tokens, each refilling at ``per_minute``."""

    def __init__(self, burst: int, per_minute: float) -> None:
        self.burst = float(burst)
        self.rate = per_minute / 60.0
        self._state: dict[int, tuple[float, float]] = {}  # key -> (tokens, as of)

    def _level(self, key: int, now: float) -> float:
        tokens, at = self._state.get(key, (self.burst, now))
        return min(self.burst, tokens + (now - at) * self.rate)

    def has(self, key: int, now: float) -> bool:
        return self._level(key, now) >= 1.0

    def take(self, key: int, now: float) -> None:
        self._state[key] = (self._level(key, now) - 1.0, now)
        if len(self._state) > _PRUNE_AT:
            self._state = {
                k: v for k, v in self._state.items() if self._level(k, now) < self.burst
            }


_members = TokenBucket(MEMBER_BURST, MEMBER_PER_MINUTE)
_servers = TokenBucket(SERVER_BURST, SERVER_PER_MINUTE)
# Members refused since their last reply who have already been told so.
_told: set[int] = set()


def take_reply(user_id: int, guild_id: int) -> bool:
    """Spend one reply for ``user_id`` in ``guild_id`` (0 for a DM), or return False, and
    spend nothing, when either of their buckets is empty."""
    now = time.monotonic()
    if not (_members.has(user_id, now) and _servers.has(guild_id, now)):
        return False
    _members.take(user_id, now)
    _servers.take(guild_id, now)
    _told.discard(user_id)
    return True


def first_refusal(user_id: int) -> bool:
    """Whether this is the first refusal since ``user_id``'s last reply. They're told once
    that they're going too fast; every message after that, until a reply goes through
    again, is left unanswered, so the notice can't become the flood."""
    if user_id in _told:
        return False
    _told.add(user_id)
    return True
