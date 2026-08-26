"""Emulated members: bot accounts Olisar treats as people.

Empty in every real deployment. ``OLISAR_PEER_BOT_IDS`` is set only by the sandbox
harness (see ``sandbox/``), which runs a fleet of Discord bots that play server
members so Olisar can be exercised against realistic multi-party conversation. With
the list empty, :func:`is_member_author` is exactly ``not author.bot`` and every call
site behaves as it did before this module existed.

Why a shared predicate rather than four inline checks: the bot-author test is made in
four places that must agree with each other. The search index records bot messages and
excludes them only from *captioning*, while conversational memory drops them entirely —
so allowlisting an emulator in one place and not the others makes ``search_messages``
return messages that ``build_contents`` cannot see. That looks like a retrieval bug, and
it would be diagnosed as one. One predicate, one answer.

Olisar's own account is deliberately not exempt here: the conversation cog drops its own
messages by id before this is consulted, and the sandbox CLI refuses to allowlist it.
"""

from __future__ import annotations

from olisar.config import settings


def peer_bot_ids() -> frozenset[int]:
    """The allowlisted emulator ids. Empty outside the sandbox."""
    return frozenset(settings.peer_bot_ids or ())


def is_peer_bot(user: object) -> bool:
    """Whether ``user`` is an allowlisted member-emulator (and not a human)."""
    if not getattr(user, "bot", False):
        return False
    return _user_id(user) in peer_bot_ids()


def is_member_author(user: object) -> bool:
    """Whether to treat ``user`` as a person — a real human, or an allowlisted emulator.

    This is the replacement for a bare ``not author.bot``: same answer in production,
    and in the sandbox it lets the emulator fleet reach conversational memory, triggers,
    profiles, and proactivity the way real members do.
    """
    if not getattr(user, "bot", False):
        return True
    return _user_id(user) in peer_bot_ids()


def _user_id(user: object) -> int:
    try:
        return int(getattr(user, "id", 0) or 0)
    except (TypeError, ValueError):
        return 0
