"""Server-side sessions with a signed cookie holding the session id.

The cookie carries only an opaque, signed sid; the actual session (and its
expiry) lives in the `session` table, so sessions are revocable.
"""

from __future__ import annotations

import secrets
from datetime import timedelta, timezone

from itsdangerous import BadSignature, URLSafeSerializer

from olisar import runtime_config
from olisar.db.engine import session_scope
from olisar.db.models import AdminUser, Session, utcnow

COOKIE_NAME = "olisar_session"
SESSION_TTL_DAYS = 14

# Built lazily from the resolved session secret (which may be auto-generated on the
# first run, after this module is imported), and rebuilt if the secret changes.
_serializer: URLSafeSerializer | None = None
_serializer_secret: str | None = None


async def _get_serializer() -> URLSafeSerializer:
    global _serializer, _serializer_secret
    secret = await runtime_config.session_secret()
    if _serializer is None or _serializer_secret != secret:
        _serializer = URLSafeSerializer(secret, salt="olisar-session")
        _serializer_secret = secret
    return _serializer


async def sign_sid(sid: str) -> str:
    return (await _get_serializer()).dumps(sid)


async def _unsign_sid(token: str) -> str | None:
    try:
        return (await _get_serializer()).loads(token)
    except BadSignature:
        return None


async def create_session(admin_user_id: int) -> str:
    sid = secrets.token_urlsafe(32)
    async with session_scope() as session:
        session.add(
            Session(
                sid=sid,
                admin_user_id=admin_user_id,
                expires_at=utcnow() + timedelta(days=SESSION_TTL_DAYS),
                csrf_secret=secrets.token_urlsafe(16),
            )
        )
    return sid


async def get_admin_for_token(token: str) -> AdminUser | None:
    sid = await _unsign_sid(token)
    if not sid:
        return None
    async with session_scope() as session:
        sess = await session.get(Session, sid)
        if sess is None:
            return None
        expires = sess.expires_at
        if expires.tzinfo is None:  # SQLite returns naive datetimes
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < utcnow():
            await session.delete(sess)
            return None
        return await session.get(AdminUser, sess.admin_user_id)


async def delete_session(token: str) -> None:
    sid = await _unsign_sid(token)
    if not sid:
        return
    async with session_scope() as session:
        sess = await session.get(Session, sid)
        if sess is not None:
            await session.delete(sess)
