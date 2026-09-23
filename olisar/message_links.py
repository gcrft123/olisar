"""Discord message jump-links: who may be shown one, and keeping invented ones out of replies.

Olisar cites a past message by pasting its jump-link, which Discord renders as a chip that
opens the message. Two things have to hold for that to be safe to do routinely:

* The person being answered can open it. Search and recall draw on everything Olisar can
  read, which is wider than what most members can, so every candidate goes through a
  ``ChannelFilter`` for the person asking before the model sees it. See
  :func:`channel_filter`.
* The link is real. A model copying a 19-digit snowflake can slip a digit, or write a link
  to a message it was never shown. :func:`strip_unoffered_links` removes any message link
  in a reply that wasn't in what the model was given, so a citation either opens the
  message it names or isn't there.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable

log = logging.getLogger("olisar.links")

# Given a set of channel ids, the subset whose messages the person being answered can open.
ChannelFilter = Callable[[set[int]], Awaitable[set[int]]]

_LINK = (
    r"https?://(?:www\.|ptb\.|canary\.)?discord(?:app)?\.com/channels/"
    r"(?:\d+|@me)/\d+/\d+"
)
_LINK_IDS = re.compile(
    r"https?://(?:www\.|ptb\.|canary\.)?discord(?:app)?\.com/channels/"
    r"(\d+|@me)/(\d+)/(\d+)"
)
# Each form a link is written in, matched whole so that removing the link doesn't leave
# `[label]()`, `<>` or `()` behind. A bare link also takes the separator in front of it
# ("it's here: <link>" loses the colon along with the link).
_CITATION = re.compile(
    r"\[(?P<label>[^\]\n]*)\]\(\s*<?(?P<masked>" + _LINK + r")>?\s*\)"
    r"|(?P<lead>[ \t]*(?:[:→–—-][ \t]*)?)"
    r"(?:\(<?(?P<paren>" + _LINK + r")>?\)|<(?P<angle>" + _LINK + r")>|(?P<bare>" + _LINK + r"))"
)


def message_link(guild_id: int, channel_id: int, message_id: int) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def link_ids(text: str) -> set[tuple[str, str, str]]:
    """The (guild, channel, message) of every message link in ``text``.

    Compared by id rather than by string, so a ptb/canary/discordapp form of a link the
    model was given still counts as given."""
    return {m.groups() for m in _LINK_IDS.finditer(text or "")}


def strip_unoffered_links(
    reply: str, offered: set[tuple[str, str, str]]
) -> tuple[str, list[str]]:
    """Remove every message link in ``reply`` whose ids aren't in ``offered``.

    Returns the cleaned reply and the links taken out. A masked link keeps its label."""
    removed: list[str] = []

    def _swap(m: re.Match) -> str:
        url = next(g for g in (m["masked"], m["paren"], m["angle"], m["bare"]) if g)
        if link_ids(url) <= offered:
            return m.group(0)
        removed.append(url)
        return m["label"] if m["masked"] is not None else ""

    cleaned = _CITATION.sub(_swap, reply or "")
    if not removed:
        return reply, []
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+([,.!?;])", r"\1", cleaned)
    cleaned = "\n".join(line.rstrip() for line in cleaned.split("\n")).strip()
    return cleaned, removed


def channel_filter(
    actions: object | None, *, guild_id: int, requester_id: int, here: int
) -> ChannelFilter:
    """The ChannelFilter for one reply: what ``requester_id`` can open in ``guild_id``, plus
    ``here``, the channel the reply is going to.

    ``actions`` is the reply's DiscordActions. Without one there's nothing to ask, so only
    ``here`` passes, and the same goes for a failed check. Failing closed is the point: a
    filter that let everything through whenever it couldn't check would be the unfiltered
    index again.

    It checks the asker, not everyone who will read the reply, and that's deliberate. A
    member who can open a private channel and asks about it in a public one gets the answer
    and the link there, the same as if they'd pasted the link themselves."""

    async def readable(channel_ids: set[int]) -> set[int]:
        allowed = {here} & channel_ids if here else set()
        rest = channel_ids - allowed
        if not rest or actions is None or not guild_id:
            return allowed
        try:
            allowed |= await actions.readable_channels(
                guild_id, rest, requester_id=requester_id
            )
        except Exception:
            log.exception("couldn't check channel access; keeping only this channel")
        return allowed

    return readable
