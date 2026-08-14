"""Server-side sessions with a signed cookie holding the session id.

The cookie carries only an opaque, signed sid; the actual session (and its
expiry) lives in the `session` table, so sessions are revocable.

Two parallel families live here, kept deliberately separate rather than merged behind a
role flag: **admin** sessions (the console) and **member** sessions (the member portal —
see api/routers/member.py). They use different tables, different cookies and different
signing salts, so a member token is not merely rejected by ``require_admin`` — it can't
even be unsigned by it.
"""

from __future__ import annotations

import secrets
from datetime import timedelta, timezone

from itsdangerous import BadSignature, URLSafeSerializer

from olisar import runtime_config
from olisar.db.engine import session_scope
from olisar.db.models import AdminUser, MemberSession, MemberUser, Session, utcnow

COOKIE_NAME = "olisar_session"
MEMBER_COOKIE_NAME = "olisar_member"
SESSION_TTL_DAYS = 14
# Members re-authenticate more often than operators. A portal session is reachable from any
# browser on the public tunnel URL, and its whole purpose is standing access to personal
# data, so it shouldn't outlive a forgotten laptop by two weeks.
MEMBER_SESSION_TTL_DAYS = 7

# Built lazily from the resolved session secret (which may be auto-generated on the
# first run, after this module is imported), and rebuilt if the secret changes. Keyed by
# salt: admin and member tokens are signed with the same secret but different salts, so a
# token from one family fails signature verification in the other rather than unsigning to
# a sid that then gets looked up in the wrong table.
_ADMIN_SALT = "olisar-session"
_MEMBER_SALT = "olisar-member-session"
_serializers: dict[str, URLSafeSerializer] = {}
_serializer_secret: str | None = None


async def _get_serializer(salt: str = _ADMIN_SALT) -> URLSafeSerializer:
    global _serializer_secret
    secret = await runtime_config.session_secret()
    if _serializer_secret != secret:
        _serializers.clear()
        _serializer_secret = secret
    if salt not in _serializers:
        _serializers[salt] = URLSafeSerializer(secret, salt=salt)
    return _serializers[salt]


async def sign_sid(sid: str) -> str:
    return (await _get_serializer()).dumps(sid)


async def _unsign_sid(token: str, salt: str = _ADMIN_SALT) -> str | None:
    try:
        return (await _get_serializer(salt)).loads(token)
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


# ── Member portal sessions ──────────────────────────────────────────────────────


async def sign_member_sid(sid: str) -> str:
    return (await _get_serializer(_MEMBER_SALT)).dumps(sid)


async def create_member_session(member_user_id: int) -> tuple[str, str]:
    """Create a portal session. Returns ``(sid, csrf_token)`` — the CSRF token is handed to
    the client once, by ``GET /api/member/session``, and echoed back in ``X-CSRF-Token`` on
    every mutating call (see api/auth/deps.py)."""
    sid = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(16)
    async with session_scope() as session:
        session.add(
            MemberSession(
                sid=sid,
                member_user_id=member_user_id,
                expires_at=utcnow() + timedelta(days=MEMBER_SESSION_TTL_DAYS),
                csrf_secret=csrf,
            )
        )
    return sid, csrf


async def get_member_for_token(token: str) -> tuple[MemberUser, str] | None:
    """Resolve a member cookie to ``(MemberUser, csrf_secret)``, or None if the token is
    unsigned-invalid, unknown or expired. The csrf secret rides along so the caller can
    verify a mutating request without a second query."""
    sid = await _unsign_sid(token, _MEMBER_SALT)
    if not sid:
        return None
    async with session_scope() as session:
        sess = await session.get(MemberSession, sid)
        if sess is None:
            return None
        expires = sess.expires_at
        if expires.tzinfo is None:  # SQLite returns naive datetimes
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < utcnow():
            await session.delete(sess)
            return None
        member = await session.get(MemberUser, sess.member_user_id)
        if member is None:
            return None
        return member, sess.csrf_secret


async def delete_member_session(token: str) -> None:
    sid = await _unsign_sid(token, _MEMBER_SALT)
    if not sid:
        return
    async with session_scope() as session:
        sess = await session.get(MemberSession, sid)
        if sess is not None:
            await session.delete(sess)


async def delete_member_sessions_for(member_user_id: int) -> None:
    """Drop every session for one member — used when the live re-check finds they've left
    every server Olisar is in, so revocation isn't limited to the browser that asked."""
    from sqlalchemy import delete as sa_delete

    async with session_scope() as session:
        await session.execute(
            sa_delete(MemberSession).where(MemberSession.member_user_id == member_user_id)
        )
