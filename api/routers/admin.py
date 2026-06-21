"""Admin config endpoints. Everything per-server is scoped to the guild named in
the ``X-Guild-Id`` header (authorized by ``require_guild_admin``) and read live by
the bot from the same SQLite DB (WAL) — so a save takes effect on the next reply /
scan, no restart. Account- and global-scope routes (me, models, keys, guilds) use
``require_admin``."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from api.auth.deps import GuildContext, require_admin, require_guild_admin
from api.trust import is_local_request
from api.schemas import (
    ApiKeysIn,
    ChannelModeIn,
    ConfigIn,
    ExtensionToggleIn,
    FactIn,
    PersonaIn,
    ProactivityIn,
    SandboxChatIn,
)
from olisar import runtime_keys
from olisar.audit import record_audit
from olisar.config import settings
from olisar.db.engine import session_scope
from olisar.db.models import (
    AdminUser,
    AppSecret,
    ChannelAllowlist,
    ChannelMode,
    GeminiUsage,
    Guild,
    GuildChannelInfo,
    ExtensionState,
    GuildConfig,
    GuildFact,
    GuildRole,
    Persona,
    ProactivityConfig,
    ProactivityLevel,
    UserMemory,
    UserProfile,
)
from olisar.gemini.models import RANKED
from olisar.messages import DEFAULT_COMMAND_MESSAGES, PLACEHOLDERS

router = APIRouter(prefix="/api", tags=["admin"])
log = logging.getLogger("olisar.api.admin")


def _apply(obj, data: dict) -> None:
    for field, value in data.items():
        setattr(obj, field, value)


@router.get("/me")
async def me(admin: AdminUser = Depends(require_admin)):
    return {
        "id": str(admin.discord_user_id),
        "username": admin.username,
        "granted_via": admin.granted_via.value,
    }


@router.get("/guilds")
async def get_guilds(admin: AdminUser = Depends(require_admin)):
    """The servers this admin may manage: every guild Olisar is in if allowlisted,
    otherwise the ones where they have Manage Server. Drives the dashboard switcher."""
    managed = set(admin.managed_guild_ids or [])
    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(Guild).where(Guild.active.is_(True)).order_by(Guild.name)
            )
        ).all()
    return [
        {"id": str(g.id), "name": g.name or str(g.id), "icon": g.icon}
        for g in rows
        if admin.is_allowlisted or str(g.id) in managed
    ]


@router.get("/models")
async def models(admin: AdminUser = Depends(require_admin)):
    """The ranked Gemini model fallback chain (best -> worst)."""
    return [{"name": m.name, "label": m.label} for m in RANKED]


@router.get("/persona")
async def get_persona(gctx: GuildContext = Depends(require_guild_admin)):
    async with session_scope() as session:
        p = await session.get(Persona, gctx.guild_id)
        if p is None:
            raise HTTPException(status_code=404, detail="persona not initialized")
        return {
            "name": p.name,
            "system_prompt": p.system_prompt,
            "tone_notes": p.tone_notes,
            "desired_bio": p.desired_bio,
        }


@router.put("/persona")
async def put_persona(body: PersonaIn, gctx: GuildContext = Depends(require_guild_admin)):
    data = body.model_dump(exclude_unset=True)
    async with session_scope() as session:
        p = await session.get(Persona, gctx.guild_id)
        if p is None:
            p = Persona(guild_id=gctx.guild_id)
            session.add(p)
        _apply(p, data)
        p.updated_by = gctx.admin.discord_user_id
        await record_audit(
            session, actor=gctx.admin.discord_user_id, action="update_persona",
            target_type="persona", target_id=gctx.guild_id, after=data,
        )
    # The profile bio is the bot's bot-wide Application Description (About Me), so it
    # can only be driven by one persona — the home/target guild's. Apply it live when
    # that guild's persona is saved; other guilds keep the field as a stored draft.
    if "desired_bio" in data and settings.target_guild_id and gctx.guild_id == settings.target_guild_id:
        from olisar.discord_bio import apply_bot_bio
        from olisar.runtime_config import discord_token
        try:
            await apply_bot_bio(await discord_token(), data.get("desired_bio") or "")
        except Exception:
            log.exception("applying bot bio on persona save failed")
    return {"ok": True}


@router.post("/sandbox/chat")
async def sandbox_chat(body: SandboxChatIn, gctx: GuildContext = Depends(require_guild_admin)):
    """Enclosed test chat: generate a reply with the live persona, KB, and tools, but
    no memory — nothing is read from or written to the server's glossary/memory. The
    transcript is supplied by the client (server keeps no state)."""
    # Lazy import: the pipeline pulls in the whole bot stack; keep it off the API's
    # import path until an admin actually opens the test chat.
    from olisar.pipeline import generate_sandbox_reply

    msgs = [{"role": m.role, "content": m.content} for m in body.messages if m.content.strip()]
    if not msgs:
        raise HTTPException(status_code=400, detail="no messages")
    # Bound the transcript so an admin can't push an unbounded context at the model.
    msgs = msgs[-40:]
    async with session_scope() as session:
        reply = await generate_sandbox_reply(session, guild_id=gctx.guild_id, messages=msgs)
    return {"reply": reply}


@router.get("/config")
async def get_config(gctx: GuildContext = Depends(require_guild_admin)):
    async with session_scope() as session:
        c = await session.get(GuildConfig, gctx.guild_id)
        if c is None:
            raise HTTPException(status_code=404, detail="config not initialized")
        return {
            "name_triggers": c.name_triggers,
            "reply_in_dms": c.reply_in_dms,
            "loose_msg_enabled": c.loose_msg_enabled,
            "default_model": c.default_model,
            "grounding_enabled": c.grounding_enabled,
            "grounding_daily_cap": c.grounding_daily_cap,
            "summary_token_threshold": c.summary_token_threshold,
            "glossary_mine_token_threshold": c.glossary_mine_token_threshold,
            "user_persona_msg_threshold": c.user_persona_msg_threshold,
            "presence_tools_enabled": c.presence_tools_enabled,
            "allowed_role_ids": [str(r) for r in (c.allowed_role_ids or [])],
            "blocked_role_ids": [str(r) for r in (c.blocked_role_ids or [])],
        }


@router.put("/config")
async def put_config(body: ConfigIn, gctx: GuildContext = Depends(require_guild_admin)):
    data = body.model_dump(exclude_unset=True)
    async with session_scope() as session:
        c = await session.get(GuildConfig, gctx.guild_id)
        if c is None:
            c = GuildConfig(guild_id=gctx.guild_id)
            session.add(c)
        _apply(c, data)
        c.version = (c.version or 1) + 1
        await record_audit(
            session, actor=gctx.admin.discord_user_id, action="update_config",
            target_type="guild_config", target_id=gctx.guild_id, after=data,
        )
    return {"ok": True}


# ── API keys are global (one set powers the whole bot), so these stay session-only ──
_KEY_FIELDS = (
    "gemini_api_key",
    "cloudflare_account_id",
    "cloudflare_api_token",
    "uex_api_key",
)


@router.get("/keys")
async def get_keys(request: Request, admin: AdminUser = Depends(require_admin)):
    """Per-key status, plus a ``value`` that autofills the field from the operator's
    environment — but ONLY on a local (loopback) request, the same gate the setup
    wizard uses, so secrets are never sent to a remote (tunnel) browser."""
    local = is_local_request(request)
    async with session_scope() as session:
        row = await session.get(AppSecret, 1)
        return {
            f: {
                "dashboard": bool((getattr(row, f, "") or "") if row else ""),
                "env": bool(getattr(settings, f, "") or ""),
                "value": (getattr(settings, f, "") or "") if local else "",
            }
            for f in _KEY_FIELDS
        }


@router.put("/keys")
async def put_keys(body: ApiKeysIn, admin: AdminUser = Depends(require_admin)):
    """Store any non-empty submitted keys (blank fields are left unchanged)."""
    data = body.model_dump(exclude_unset=True)
    updates = {
        k: v.strip() for k, v in data.items()
        if k in _KEY_FIELDS and isinstance(v, str) and v.strip()
    }
    if updates:
        async with session_scope() as session:
            row = await session.get(AppSecret, 1)
            if row is None:
                row = AppSecret(id=1)
                session.add(row)
            for k, v in updates.items():
                setattr(row, k, v)
            await record_audit(
                session, actor=admin.discord_user_id, action="update_keys",
                target_type="app_secret", target_id=1,
                after={k: "***" for k in updates},  # never log the secret values
            )
        runtime_keys.invalidate()
    return {"ok": True}


@router.delete("/keys/{field}")
async def clear_key(field: str, admin: AdminUser = Depends(require_admin)):
    """Clear one stored key so it falls back to .env (or off)."""
    if field not in _KEY_FIELDS:
        raise HTTPException(status_code=404, detail="unknown key")
    async with session_scope() as session:
        row = await session.get(AppSecret, 1)
        if row is not None and getattr(row, field, ""):
            setattr(row, field, "")
            await record_audit(
                session, actor=admin.discord_user_id, action="clear_key",
                target_type="app_secret", target_id=1, after={field: ""},
            )
    runtime_keys.invalidate()
    return {"ok": True}


@router.get("/messages")
async def get_messages(gctx: GuildContext = Depends(require_guild_admin)):
    """Per-command reply text: default, current override (if any), placeholders."""
    async with session_scope() as session:
        c = await session.get(GuildConfig, gctx.guild_id)
        custom = (c.command_messages if c and c.command_messages else {})
    return {
        key: {
            "default": default,
            "custom": custom.get(key),
            "placeholders": PLACEHOLDERS.get(key, []),
        }
        for key, default in DEFAULT_COMMAND_MESSAGES.items()
    }


@router.put("/messages")
async def put_messages(body: dict, gctx: GuildContext = Depends(require_guild_admin)):
    """Set/clear command-message overrides. A null/empty value reverts to default."""
    async with session_scope() as session:
        c = await session.get(GuildConfig, gctx.guild_id)
        if c is None:
            c = GuildConfig(guild_id=gctx.guild_id)
            session.add(c)
        current = dict(c.command_messages or {})
        for key, value in body.items():
            if key not in DEFAULT_COMMAND_MESSAGES:
                continue  # ignore unknown keys
            if value is None or (isinstance(value, str) and not value.strip()):
                current.pop(key, None)
            else:
                current[key] = value
        c.command_messages = current  # reassign so SQLAlchemy detects the change
        c.version = (c.version or 1) + 1
        await record_audit(
            session, actor=gctx.admin.discord_user_id, action="update_command_messages",
            target_type="guild_config", target_id=gctx.guild_id, after={"keys": list(body.keys())},
        )
    return {"ok": True}


@router.get("/proactivity")
async def get_proactivity(gctx: GuildContext = Depends(require_guild_admin)):
    async with session_scope() as session:
        p = await session.get(ProactivityConfig, gctx.guild_id)
        if p is None:
            raise HTTPException(status_code=404, detail="proactivity not initialized")
        return {
            "enabled": p.enabled,
            "level": p.level.value,
            "channel_cooldown_sec": p.channel_cooldown_sec,
            "user_cooldown_sec": p.user_cooldown_sec,
            "global_cooldown_sec": p.global_cooldown_sec,
            "confidence_threshold": p.confidence_threshold,
            "max_per_hour": p.max_per_hour,
            "quiet_hours": p.quiet_hours,
            "allowed_channels": p.allowed_channels,
            "reaction_enabled": p.reaction_enabled,
            "reaction_threshold": p.reaction_threshold,
            "reaction_cooldown_sec": p.reaction_cooldown_sec,
            "reaction_max_per_hour": p.reaction_max_per_hour,
        }


@router.put("/proactivity")
async def put_proactivity(body: ProactivityIn, gctx: GuildContext = Depends(require_guild_admin)):
    data = body.model_dump(exclude_unset=True)
    if "level" in data:
        try:
            data["level"] = ProactivityLevel(data["level"])
        except ValueError:
            raise HTTPException(status_code=400, detail="level must be off/low/med/high")
    async with session_scope() as session:
        p = await session.get(ProactivityConfig, gctx.guild_id)
        if p is None:
            p = ProactivityConfig(guild_id=gctx.guild_id)
            session.add(p)
        _apply(p, data)
        audit_after = {**data, "level": data["level"].value} if "level" in data else data
        await record_audit(
            session, actor=gctx.admin.discord_user_id, action="update_proactivity",
            target_type="proactivity_config", target_id=gctx.guild_id, after=audit_after,
        )
    return {"ok": True}


@router.get("/channels")
async def get_channels(gctx: GuildContext = Depends(require_guild_admin)):
    """Full channel roster (synced by the bot) with each channel's current mode."""
    guild = gctx.guild_id
    async with session_scope() as session:
        allow = (
            await session.scalars(
                select(ChannelAllowlist).where(ChannelAllowlist.guild_id == guild)
            )
        ).all()
        roster = (
            await session.scalars(
                select(GuildChannelInfo)
                .where(
                    GuildChannelInfo.guild_id == guild,
                    GuildChannelInfo.kind != "thread",  # threads inherit; not pickable
                )
                .order_by(GuildChannelInfo.position)
            )
        ).all()

    modes = {r.channel_id: r.mode.value for r in allow}
    if roster:
        out = [
            {
                "channel_id": str(c.channel_id),
                "name": c.name,
                "category": c.category,
                "mode": modes.pop(c.channel_id, "off"),
                "kind": c.kind,
                "indexed": c.index_enabled,
            }
            for c in roster
        ]
        out.extend(
            {"channel_id": str(cid), "name": str(cid), "category": "", "mode": mode, "indexed": True}
            for cid, mode in modes.items()
            if mode != "off"
        )
        return out
    return [
        {"channel_id": str(r.channel_id), "name": str(r.channel_id), "category": "", "mode": r.mode.value, "indexed": True}
        for r in allow
    ]


@router.put("/channels")
async def put_channel(body: ChannelModeIn, gctx: GuildContext = Depends(require_guild_admin)):
    from olisar.memory.writer import delete_channel_index, reindex_channel

    removed: int | None = None
    async with session_scope() as session:
        if body.mode is not None:
            try:
                mode = ChannelMode(body.mode)
            except ValueError:
                raise HTTPException(
                    status_code=400, detail="mode must be off/memory/respond/both/resource/feed"
                )
            row = await session.scalar(
                select(ChannelAllowlist).where(
                    ChannelAllowlist.guild_id == gctx.guild_id,
                    ChannelAllowlist.channel_id == body.channel_id,
                )
            )
            if row is None:
                row = ChannelAllowlist(
                    guild_id=gctx.guild_id, channel_id=body.channel_id,
                    added_by=gctx.admin.discord_user_id,
                )
                session.add(row)
            row.mode = mode
            await record_audit(
                session, actor=gctx.admin.discord_user_id, action="set_channel_mode",
                target_type="channel", target_id=body.channel_id, after={"mode": body.mode},
            )

        if body.indexed is not None:
            ci = await session.get(GuildChannelInfo, body.channel_id)
            if ci is None or ci.guild_id != gctx.guild_id:
                raise HTTPException(status_code=404, detail="unknown channel")
            ci.index_enabled = body.indexed
            if body.indexed:
                await reindex_channel(session, gctx.guild_id, body.channel_id)  # re-index its history
            else:
                removed = await delete_channel_index(session, gctx.guild_id, body.channel_id)
            await record_audit(
                session, actor=gctx.admin.discord_user_id, action="set_channel_indexing",
                target_type="channel", target_id=body.channel_id,
                after={"indexed": body.indexed, "removed": removed},
            )

    out: dict = {"ok": True}
    if removed is not None:
        out["removed"] = removed
    return out


@router.get("/roles")
async def get_roles(gctx: GuildContext = Depends(require_guild_admin)):
    """The guild's roles (synced by the bot), highest first — for the access picker."""
    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(GuildRole)
                .where(GuildRole.guild_id == gctx.guild_id)
                .order_by(GuildRole.position.desc())
            )
        ).all()
    return [
        {"role_id": str(r.role_id), "name": r.name, "color": r.color, "position": r.position}
        for r in rows
    ]


@router.get("/profiles")
async def get_profiles(gctx: GuildContext = Depends(require_guild_admin)):
    """The private profiles Olisar has built of members in this server — roles, the
    impression synthesized from their messages, and remembered facts. Opted-out
    members are excluded. Sorted so members it's actually learned about come first."""
    async with session_scope() as session:
        profs = (
            await session.scalars(
                select(UserProfile).where(
                    UserProfile.guild_id == gctx.guild_id,
                    UserProfile.memory_opt_out.is_(False),
                )
            )
        ).all()
        mems = (
            await session.scalars(
                select(UserMemory).where(UserMemory.guild_id == gctx.guild_id)
            )
        ).all()

    by_user: dict[int, list] = {}
    for m in mems:
        by_user.setdefault(m.user_id, []).append(m)

    out = []
    for p in profs:
        ms = sorted(by_user.get(p.user_id, []), key=lambda x: x.salience, reverse=True)
        out.append({
            "user_id": str(p.user_id),
            "display_name": p.display_name or str(p.user_id),
            "roles": [r.get("name") for r in (p.roles or []) if r.get("name")],
            "impression": p.persona_summary or "",
            "messages_since_persona": p.messages_since_persona,
            "first_seen": p.first_seen.isoformat() if p.first_seen else None,
            "last_seen": p.last_seen.isoformat() if p.last_seen else None,
            "memories": [
                {
                    "kind": x.kind.value,
                    "content": x.content,
                    "created_at": x.created_at.isoformat() if x.created_at else None,
                }
                for x in ms
            ],
        })
    # Impressions first, then members it only remembers facts about, then the rest —
    # each tier most-recently-seen first.
    out.sort(
        key=lambda d: (bool(d["impression"]), bool(d["memories"]), d["last_seen"] or ""),
        reverse=True,
    )
    return out


@router.post("/profiles/{user_id}/impression")
async def build_impression(user_id: int, gctx: GuildContext = Depends(require_guild_admin)):
    """Build/refresh one member's impression on demand — from their last 60 messages,
    falling back to the all-channel index when conversation memory is thin."""
    from olisar.memory.personas import build_persona_now

    async with session_scope() as session:
        result = await build_persona_now(session, guild_id=gctx.guild_id, user_id=user_id)
        if result.get("ok"):
            await record_audit(
                session, actor=gctx.admin.discord_user_id, action="build_impression",
                target_type="user_profile", target_id=user_id,
            )
    return result


@router.get("/extensions")
async def get_extensions(gctx: GuildContext = Depends(require_guild_admin)):
    """The extension catalog (built-in + SDK) with this server's enabled state."""
    from olisar.db.models import ExtensionPackage
    from olisar.extensions import all_extensions, user_registry

    async with session_scope() as session:
        await user_registry.load(session)  # warm the SDK-extension cache
        states = {
            r.key: r.enabled
            for r in (
                await session.scalars(
                    select(ExtensionState).where(ExtensionState.guild_id == gctx.guild_id)
                )
            ).all()
        }
        pkgs = {p.key: p for p in (await session.scalars(select(ExtensionPackage))).all()}

    def _signed_by(pkg):
        if pkg is None or not pkg.publisher_key:
            return None
        from olisar.extensions import signing
        return signing.fingerprint(pkg.publisher_key)

    def _entry(e):
        pkg = pkgs.get(e.key)
        manifest = (pkg.manifest if pkg else {}) or {}
        return {
            "key": e.key,
            "name": e.name,
            "description": e.description,
            "category": e.category,
            "enabled": states.get(e.key, e.default_enabled),
            "default_enabled": e.default_enabled,
            "kind": pkg.kind if pkg else "builtin",  # SDK kind ("user"/"builtin"); Python = builtin
            "editable": bool(pkg and pkg.kind == "user"),  # "Custom" — deletable, gets the badge
            "user_modified": bool(pkg and pkg.user_modified),
            "has_code": pkg is not None,  # any SDK extension (built-in or user) is editable in-place
            # Provenance: where it came from (local | imported | marketplace) and, for an
            # imported package, the capabilities its author asked for vs. what was granted.
            "origin": (pkg.origin if pkg else "builtin"),
            "publisher": (pkg.publisher_name if pkg else None),
            "signed_by": _signed_by(pkg),
            "signature_verified": (pkg.signature_verified if pkg else None),
            # What the extension contributes — surfaced in the catalog detail panel.
            "tools": [t.declaration.name for t in e.tools],
            "commands": [c.get("name") for c in manifest.get("commands", []) if c.get("name")],
            "permissions": list(pkg.permissions) if pkg else [],
            "requested_permissions": list(pkg.requested_permissions) if pkg else [],
            "behavior": bool(e.system_note),
            "settings_schema": manifest.get("settings_schema") or None,
        }

    return [_entry(e) for e in all_extensions()]


@router.put("/extensions")
async def put_extension(body: ExtensionToggleIn, gctx: GuildContext = Depends(require_guild_admin)):
    """Toggle one extension on/off for this server. Takes effect on the next reply.
    On an off→on transition, fires the extension's on_enable hook for this guild."""
    from olisar.extensions import get_extension, user_registry

    async with session_scope() as session:
        await user_registry.load(session)  # so SDK extensions resolve too
        ext = get_extension(body.key)
        if ext is None:
            raise HTTPException(status_code=404, detail="unknown extension")
        row = await session.get(ExtensionState, (gctx.guild_id, body.key))
        was_enabled = row.enabled if row is not None else ext.default_enabled
        if row is None:
            row = ExtensionState(guild_id=gctx.guild_id, key=body.key)
            session.add(row)
        row.enabled = body.enabled
        await record_audit(
            session, actor=gctx.admin.discord_user_id, action="toggle_extension",
            target_type="extension", target_id=body.key, after={"enabled": body.enabled},
        )
        if body.enabled and not was_enabled and ext.on_enable is not None:
            try:
                await ext.on_enable(session, gctx.guild_id)
            except Exception:
                log.exception("on_enable hook failed for extension %s", body.key)
    return {"ok": True}


@router.get("/extensions/{key}/settings")
async def get_extension_settings(key: str, gctx: GuildContext = Depends(require_guild_admin)):
    """Per-extension config (e.g. the welcome message's channel + prompt)."""
    from olisar.extensions import get_extension, user_registry

    async with session_scope() as session:
        await user_registry.load(session)
        if get_extension(key) is None:
            raise HTTPException(status_code=404, detail="unknown extension")
        row = await session.get(ExtensionState, (gctx.guild_id, key))
        return {"settings": (row.settings if row and row.settings else {})}


@router.put("/extensions/{key}/settings")
async def put_extension_settings(
    key: str, body: dict, gctx: GuildContext = Depends(require_guild_admin)
):
    """Merge new values into an extension's per-guild settings JSON."""
    from olisar.extensions import get_extension, user_registry

    async with session_scope() as session:
        await user_registry.load(session)
        if get_extension(key) is None:
            raise HTTPException(status_code=404, detail="unknown extension")
        row = await session.get(ExtensionState, (gctx.guild_id, key))
        if row is None:
            row = ExtensionState(guild_id=gctx.guild_id, key=key)
            session.add(row)
        row.settings = {**(row.settings or {}), **(body or {})}
        await record_audit(
            session, actor=gctx.admin.discord_user_id, action="update_extension_settings",
            target_type="extension", target_id=key, after=(body or {}),
        )
    return {"ok": True}


@router.get("/facts")
async def get_facts(gctx: GuildContext = Depends(require_guild_admin)):
    """The guild glossary — durable server lore Olisar carries into every reply."""
    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(GuildFact)
                .where(GuildFact.guild_id == gctx.guild_id)
                .order_by(GuildFact.mentions.desc(), GuildFact.updated_at.desc())
            )
        ).all()
    return [
        {
            "id": r.id,
            "subject": r.subject,
            "fact": r.fact,
            "mentions": r.mentions,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]


@router.post("/facts")
async def add_fact(body: FactIn, gctx: GuildContext = Depends(require_guild_admin)):
    fact = body.fact.strip()
    if not fact:
        raise HTTPException(status_code=400, detail="fact is required")
    subject = (body.subject or "").strip() or fact.split(" ", 1)[0][:128]
    async with session_scope() as session:
        row = GuildFact(guild_id=gctx.guild_id, subject=subject[:128], fact=fact)
        session.add(row)
        await record_audit(
            session, actor=gctx.admin.discord_user_id, action="add_guild_fact",
            target_type="guild_fact", target_id=gctx.guild_id,
            after={"subject": subject, "fact": fact},
        )
    return {"ok": True}


@router.delete("/facts/{fact_id}")
async def delete_fact(fact_id: int, gctx: GuildContext = Depends(require_guild_admin)):
    async with session_scope() as session:
        row = await session.get(GuildFact, fact_id)
        if row is None or row.guild_id != gctx.guild_id:
            raise HTTPException(status_code=404, detail="fact not found")
        await session.delete(row)
        await record_audit(
            session, actor=gctx.admin.discord_user_id, action="delete_guild_fact",
            target_type="guild_fact", target_id=fact_id,
        )
    return {"ok": True}


@router.get("/stats")
async def get_stats(admin: AdminUser = Depends(require_admin)):
    """Gemini usage is bot-wide (one quota), so this stays account-scoped."""
    today = datetime.now(timezone.utc).date()
    async with session_scope() as session:
        rows = (await session.scalars(select(GeminiUsage))).all()
    by_model: dict[str, dict] = {}
    today_requests = today_grounding = 0
    for r in rows:
        agg = by_model.setdefault(r.model, {"requests": 0, "tokens": 0, "grounding": 0})
        agg["requests"] += r.request_count
        agg["tokens"] += r.token_count
        agg["grounding"] += r.grounding_count
        if r.day == today:
            today_requests += r.request_count
            today_grounding += r.grounding_count
    return {
        "today": {"requests": today_requests, "grounding": today_grounding},
        "by_model": by_model,
    }
