"""The memory replay, for the console (olisar/replay/ builds it; bot/cogs/replay.py runs it).

Anyone who can manage the server in the console can open it, start one, watch one someone
else started, and answer its last question: the operator and every admin with Manage
Server there. One runs at a time per server.

    GET  /api/replay          what the start sheet, remembering and Play show
    GET  /api/replay/status   the run in progress, for the remembering screen to poll
    POST /api/replay/start    start one ({"anyway": true} while the index is still reading)
    POST /api/replay/retry    carry on a run that failed or stopped, from where it stopped
    GET  /api/replay/report   the finished replay, redacted for who can be shown today
    POST /api/replay/answer   save the answer to its last question, for next year's
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from api.auth.deps import GuildContext, require_guild_admin
from api.botinfo import bot_name
from api.schemas import ReplayAnswerIn, ReplayStartIn
from olisar.audit import record_audit
from olisar.db.engine import session_scope
from olisar.db.models import Guild, ReplayRun, utcnow
from olisar.replay import preflight, runner, store
from olisar.replay.scope import aware, hidden_users

router = APIRouter(prefix="/api/replay", tags=["replay"])


def _bot(request: Request):
    supervisor = getattr(request.app.state, "bot_supervisor", None)
    bot = getattr(supervisor, "bot", None) if supervisor is not None else None
    return bot if bot is not None and bot.is_ready() else None


def _poke(request: Request) -> None:
    bot = _bot(request)
    cog = bot.get_cog("Replay") if bot is not None else None
    if cog is not None:
        cog.poke()


def _bot_ids(request: Request, guild_id: int) -> set[int]:
    """The bots in the server, for counting what a replay would read the way the bot will."""
    bot = _bot(request)
    guild = bot.get_guild(guild_id) if bot is not None else None
    if guild is None:
        return set()
    return {m.id for m in guild.members if m.bot} | ({bot.user.id} if bot.user else set())


def _gone(request: Request, guild_id: int, ids) -> set[int]:
    """Of ``ids``, who has left the server since the report was made. Only known while
    the bot holds the full member list."""
    bot = _bot(request)
    guild = bot.get_guild(guild_id) if bot is not None else None
    if guild is None or not guild.chunked:
        return set()
    return {int(i) for i in ids if str(i).isdigit() and guild.get_member(int(i)) is None}


def _iso(at: datetime | None) -> str | None:
    at = aware(at)
    return at.isoformat().replace("+00:00", "Z") if at else None


async def _run_view(session, run: ReplayRun, viewer: int, now: datetime) -> dict:
    view = store.view(run, viewer=viewer, starter=await store.admin_name(session, run.guild_id, run.started_by), now=now)
    view["eta"] = runner.eta_seconds(run, now)
    return view


def _latest_view(run: ReplayRun | None) -> dict | None:
    if run is None:
        return None
    report = run.report or {}
    return {
        "id": run.id,
        "finished_at": _iso(run.finished_at),
        "chapters": runner.chapters(report),
        "messages": (report.get("totals") or {}).get("messages"),
        "from": (report.get("range") or {}).get("from"),
        "answer": run.answer or None,
    }


@router.get("")
async def replay_state(request: Request, gctx: GuildContext = Depends(require_guild_admin)):
    now = utcnow()
    viewer = gctx.admin.discord_user_id
    async with session_scope() as session:
        guild = await session.get(Guild, gctx.guild_id)
        run = await store.active_run(session, gctx.guild_id)
        latest = await store.latest_done(session, gctx.guild_id)
        run_view = await _run_view(session, run, viewer, now) if run else None
    return {
        "server": {"id": str(gctx.guild_id), "name": guild.name if guild else ""},
        "bot": {"name": bot_name(request)},
        "run": run_view,
        "latest": _latest_view(latest),
        "preflight": await preflight.check(gctx.guild_id, now=now, bots=_bot_ids(request, gctx.guild_id)),
    }


@router.get("/status")
async def replay_status(gctx: GuildContext = Depends(require_guild_admin)):
    now = utcnow()
    async with session_scope() as session:
        run = await store.active_run(session, gctx.guild_id)
        latest = await store.latest_done(session, gctx.guild_id)
        run_view = await _run_view(session, run, gctx.admin.discord_user_id, now) if run else None
    return {"run": run_view, "latest": _latest_view(latest)}


@router.post("/start")
async def start_replay(body: ReplayStartIn, request: Request, gctx: GuildContext = Depends(require_guild_admin)):
    now = utcnow()
    async with session_scope() as session:
        if await store.active_run(session, gctx.guild_id) is not None:
            raise HTTPException(status_code=409, detail="A replay of this server is already running.")
    check = await preflight.check(gctx.guild_id, now=now, bots=_bot_ids(request, gctx.guild_id))
    refusal = {
        "short": "There isn't enough history for a replay yet.",
        "quota": "There isn't enough of today's Gemini allowance left for a replay.",
        "budget": "A replay would go past this month's Gemini budget.",
    }.get(check["state"])
    if refusal:
        raise HTTPException(status_code=400, detail=refusal)
    if check["state"] == "indexing" and not body.anyway:
        raise HTTPException(status_code=400, detail="The search index is still reading history.")
    async with session_scope() as session:
        # Checked again inside the write, so two admins pressing Start together get one run.
        if await store.active_run(session, gctx.guild_id) is not None:
            raise HTTPException(status_code=409, detail="A replay of this server is already running.")
        run = ReplayRun(guild_id=gctx.guild_id, status="queued", step="gather", started_by=gctx.admin.discord_user_id,
                        started_at=now, updated_at=now, progress={"phase": 0})
        session.add(run)
        await session.flush()
        await record_audit(session, actor=str(gctx.admin.discord_user_id), action="start_replay",
                           target_type="guild", target_id=str(gctx.guild_id), after={"anyway": body.anyway})
        view = await _run_view(session, run, gctx.admin.discord_user_id, now)
    _poke(request)
    return {"run": view}


@router.post("/retry")
async def retry_replay(request: Request, gctx: GuildContext = Depends(require_guild_admin)):
    now = utcnow()
    async with session_scope() as session:
        run = await store.active_run(session, gctx.guild_id)
        if run is None:
            raise HTTPException(status_code=404, detail="There's no replay to carry on.")
        if run.status not in ("failed", "queued", "running"):
            raise HTTPException(status_code=409, detail="This replay carries on by itself.")
        run.status, run.error, run.updated_at = "queued", "", now
        await session.flush()
        view = await _run_view(session, run, gctx.admin.discord_user_id, now)
    _poke(request)
    return {"run": view}


@router.get("/report")
async def replay_report(request: Request, run: int | None = None, gctx: GuildContext = Depends(require_guild_admin)):
    now = utcnow()
    async with session_scope() as session:
        if run is None:
            row = await store.latest_done(session, gctx.guild_id)
        else:
            row = await session.get(ReplayRun, run)
            if row is not None and (row.guild_id != gctx.guild_id or row.status != "done"):
                row = None
        if row is None or not row.report:
            raise HTTPException(status_code=404, detail="There's no finished replay of this server.")
        hidden = await hidden_users(session, gctx.guild_id, now)
        report, answer, run_id = row.report, row.answer, row.id
    hidden |= _gone(request, gctx.guild_id, (report.get("people") or {}).keys())
    out = store.present(report, hidden)
    out["run"] = run_id
    out["answer"] = answer or None
    return out


@router.post("/answer")
async def save_answer(body: ReplayAnswerIn, gctx: GuildContext = Depends(require_guild_admin)):
    text = " ".join(body.text.split())[: store.ANSWER_LIMIT]
    if not text:
        raise HTTPException(status_code=400, detail="The answer is empty.")
    async with session_scope() as session:
        run = await store.latest_done(session, gctx.guild_id)
        if run is None:
            raise HTTPException(status_code=404, detail="There's no finished replay of this server.")
        before = run.answer or None
        run.answer, run.answer_by, run.answered_at = text, gctx.admin.discord_user_id, utcnow()
        await record_audit(session, actor=str(gctx.admin.discord_user_id), action="answer_replay",
                           target_type="guild", target_id=str(gctx.guild_id), before={"answer": before}, after={"answer": text})
    return {"ok": True, "answer": text}

