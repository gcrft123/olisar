"""Request bodies: capped, and only read in full for someone signed in.

FastAPI reads and parses a route's body before any of the route's dependencies run,
authentication included. So without a gate in front of it, anyone who can reach the console
(any visitor, once remote access publishes it) could make this process buffer and parse
whatever they sent, as large as they liked, and only then be told they aren't signed in.

``BodyLimit`` decides from the headers, before a byte of the body is read:

  - A body over ``ANONYMOUS_LIMIT`` needs a credential this server issued: an admin or member
    session, the refused-sign-in cookie Feedback accepts, or the local token. Without one the
    answer is the 401 the route would have given after reading it.
  - No body may be over its route's limit: ``UPLOAD_LIMIT`` on the few routes that take a
    file, ``DEFAULT_LIMIT`` everywhere else. Past it the answer is 413.

A body sent without a Content-Length (chunked) is counted as it arrives and cut off at the
same points.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.auth.oauth import DENIED_COOKIE, denied_identity
from api.auth.sessions import (
    COOKIE_NAME,
    MEMBER_COOKIE_NAME,
    get_admin_for_token,
    get_member_for_token,
)
from api.trust import is_local_request

MB = 1024 * 1024
# Well past any settings form, persona or test-chat transcript; short of anything that costs
# this process real memory to hold.
ANONYMOUS_LIMIT = 64 * 1024
DEFAULT_LIMIT = 1 * MB
UPLOAD_LIMIT = 16 * MB

# The routes that take a file: an .olx bundle to preview or import and an extension's source
# (create, save, validate) under the first; Feedback and extension reports carry attachments,
# which the console allows up to 3 MB each (4 MB once base64-encoded).
_UPLOAD_ROUTES = (
    "/api/extensions/authoring",
    "/api/settings/feedback",
    "/api/marketplace/report",
)


def limit_for(path: str) -> int:
    """The largest body ``path`` accepts."""
    for route in _UPLOAD_ROUTES:
        if path == route or path.startswith(route + "/"):
            return UPLOAD_LIMIT
    return DEFAULT_LIMIT


async def _signed_in(request: Request) -> bool:
    """Whether the request carries a credential this server issued, checked rather than
    merely present (a made-up cookie is no one). Which routes that credential opens is still
    the route's own dependencies' call."""
    if is_local_request(request):
        return True
    cookies = request.cookies
    token = cookies.get(COOKIE_NAME)
    if token and await get_admin_for_token(token) is not None:
        return True
    token = cookies.get(MEMBER_COOKIE_NAME)
    if token and await get_member_for_token(token) is not None:
        return True
    return await denied_identity(cookies.get(DENIED_COOKIE)) is not None


def _unauthenticated() -> Response:
    return JSONResponse({"detail": "not authenticated"}, status_code=401)


def _too_large(limit: int) -> Response:
    return JSONResponse(
        {"detail": f"request body too large (the limit here is {limit // MB} MB)"},
        status_code=413,
    )


class _Refused(Exception):
    """Raised out of ``receive`` when a chunked body goes past what its sender may send."""


class BodyLimit:
    """ASGI middleware enforcing the limits above. Pure ASGI, like ``ConsoleGuard``, so a
    streamed answer and ``request.is_disconnected()`` behave as they do without it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        limit = limit_for(scope["path"])
        try:
            size = int(request.headers.get("content-length", ""))
        except ValueError:
            size = -1
        if size < 0:  # none declared (or none uvicorn would have let through)
            await self._counted(request, limit, scope, receive, send)
            return
        refusal = None
        if size > ANONYMOUS_LIMIT and not await _signed_in(request):
            refusal = _unauthenticated()
        elif size > limit:
            refusal = _too_large(limit)
        if refusal is not None:
            await refusal(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _counted(
        self, request: Request, limit: int, scope: Scope, receive: Receive, send: Send
    ) -> None:
        """A body of unknown length: pass it through while counting, and stop it at the first
        chunk that goes over. The route then fails reading it, and whatever it answers is
        replaced by the refusal."""
        received = 0
        signed_in: bool | None = None
        refusal: Response | None = None
        started = False

        async def counted_receive() -> Message:
            nonlocal received, signed_in, refusal
            if refusal is not None:
                raise _Refused()
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > ANONYMOUS_LIMIT:
                    if signed_in is None:
                        signed_in = await _signed_in(request)
                    if not signed_in:
                        refusal = _unauthenticated()
                    elif received > limit:
                        refusal = _too_large(limit)
                    if refusal is not None:
                        raise _Refused()
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if refusal is not None and not started:
                return  # the route's answer to a body it never got whole; ours goes instead
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted_receive, guarded_send)
        except Exception:
            # Whatever reading a refused body made the route raise, _Refused or its own.
            if refusal is None:
                raise
        if refusal is not None and not started:
            await refusal(scope, receive, send)
