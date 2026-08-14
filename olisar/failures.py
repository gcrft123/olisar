"""Parked blank-fallback failures, so the person one happened to can report it.

A reply that comes back as the blank fallback tells the user nothing and tells the
operator less. The pipeline already logs the reason; what was missing was a way to get
*that* log, and the prompt that produced it, from the channel where it broke to the
console where someone can act on it. Without that, a report is "the bot said its mind
went blank" and a guess at the time.

So a blank writes a row here — the prompt, where it happened, and the log tail from the
moment it broke — and the bot puts a **Report this** link on the message. The link is the
console's own URL: whoever clicks it signs in as themselves and lands wherever they belong
(admin console or member portal), with the Feedback pane already filled in. Nothing about
the failure travels in the URL; the token is a claim check, and only the account the blank
happened to can redeem it.

The rows are short-lived and self-pruning — see :func:`open_report`.
"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from olisar import logbuffer, runtime_config
from olisar.db.models import FailureReport, utcnow

log = logging.getLogger("olisar.failures")

# How long a parked failure stays claimable. Long enough that someone who hits a blank at
# midnight can report it after the weekend; short enough that an unreported prompt isn't
# kept indefinitely for a report nobody is going to file.
TTL = timedelta(days=7)

# Log lines snapshotted with the failure. The same depth the Feedback pane attaches for a
# live report, so the two produce comparable bundles.
LOG_LINES = 800

# Reports kept per person. A bot that is blanking every reply would otherwise write a row
# per attempt; the newest few are the ones worth having and the rest are the same failure.
PER_USER_CAP = 5

# Prompt text kept. A blank is a failure to answer, not a failure to read, so the opening
# of the prompt is what identifies it — and this table is not a second message store.
PROMPT_LIMIT = 2000


async def _prune(session: AsyncSession, user_id: int) -> None:
    """Drop what has aged out, then trim this user back to ``PER_USER_CAP``. Runs on the
    write path: these rows are only ever created a few at a time, so there is no sweep to
    schedule and no chance of the table growing while nothing looks at it."""
    await session.execute(delete(FailureReport).where(FailureReport.expires_at < utcnow()))
    keep = (
        await session.scalars(
            select(FailureReport.token)
            .where(FailureReport.user_id == user_id)
            .order_by(FailureReport.created_at.desc())
            .limit(PER_USER_CAP - 1)  # -1: the row about to be written takes the last slot
        )
    ).all()
    stale = delete(FailureReport).where(FailureReport.user_id == user_id)
    if keep:
        stale = stale.where(FailureReport.token.not_in(keep))
    await session.execute(stale)


async def open_report(
    session: AsyncSession,
    *,
    user_id: int,
    guild_id: int,
    channel_id: int,
    trigger: str,
    prompt: str,
) -> str:
    """Park a blank and return the console URL that reports it, or ``""`` for no link.

    Empty when remote access isn't configured: the console is loopback-only until the
    operator opens a Tailscale Funnel (or moves the bot to a server), so a link would
    resolve to ``127.0.0.1`` on the operator's machine — a dead end for everyone in the
    channel including, usually, the person who clicked it. No URL, no row, no button.
    """
    if not await runtime_config.remote_access_configured():
        return ""
    base = (await runtime_config.public_base_url()).rstrip("/")
    if not base:
        return ""

    token = secrets.token_urlsafe(24)
    try:
        await _prune(session, user_id)
        session.add(
            FailureReport(
                token=token,
                user_id=user_id,
                guild_id=guild_id,
                channel_id=channel_id,
                trigger=trigger,
                prompt=(prompt or "")[:PROMPT_LIMIT],
                logs="\n".join(logbuffer.tail(LOG_LINES)),
                expires_at=utcnow() + TTL,
            )
        )
        await session.flush()
    except Exception:
        # The reply itself already failed; failing to park the report on top of that must
        # not also cost the user their apology. Send the message without the button.
        log.exception("could not park a blank-reply report for user %s", user_id)
        return ""
    return f"{base}/?report={token}"


async def claim(session: AsyncSession, token: str, *, user_id: int) -> FailureReport | None:
    """The parked failure behind ``token``, if it belongs to ``user_id`` and is still live.

    Bound to the account it happened to, with no admin override. The button sits on a
    message in a channel, so the link is readable by everyone who can see that channel —
    but a DM prompt is private and an admin reading it here would be reading it from a
    place Discord never showed them. Whoever else clicks gets nothing.
    """
    row = await session.get(FailureReport, token)
    if row is None or row.user_id != user_id:
        return None
    expires = row.expires_at
    if expires is not None and expires.tzinfo is None:  # SQLite hands back naive datetimes
        expires = expires.replace(tzinfo=utcnow().tzinfo)
    if expires is not None and expires < utcnow():
        return None
    return row
