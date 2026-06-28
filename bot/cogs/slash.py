"""Slash commands.

`/ping`, `/ask`, `/privacy`, `/forget-me`, and the admin-only `/olisar` group
(channels, knowledge base, proactivity). Confirmation text for these commands is
rendered from admin-customizable templates (olisar/messages.py + the dashboard).
"""

from __future__ import annotations

import re
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import func, select, update

from bot.access import dm_home_guild_id, member_allowed, resolve_member
from bot.actions import BotActions
from bot.replies import chunk_text, mention_policy, sanitize_mentions
from olisar.config import settings
from olisar.db.engine import session_scope
from olisar.db.models import (
    ChannelAllowlist,
    ChannelMode,
    GuildChannelInfo,
    GuildConfig,
    KBChunk,
    KBSource,
    KBSourceType,
    KBStatus,
    ProactivityConfig,
    ProactivityLevel,
    SearchMessage,
)
from olisar.catchup import generate_catchup
from olisar.knowledge.extract import SUPPORTED_SUFFIXES
from olisar.memory.purge import forget_user
from olisar.memory.vectors import delete_embedding
from olisar.memory.writer import clear_search_index
from olisar.messages import get_command_messages, render_message
from olisar.pipeline import generate_reply
from olisar.runtime.paths import kb_uploads_dir

KB_UPLOAD_DIR = kb_uploads_dir()  # per-user data dir when packaged; repo data/ in dev
MAX_DOC_BYTES = 10 * 1024 * 1024  # 10 MB

# DMs / no-guild interactions use this sentinel (matches the conversation cog).
DM_GUILD_ID = 0


class Slash(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _msg(self, key: str, **kwargs) -> str:
        """Render an admin-customizable command reply (falls back to defaults)."""
        async with session_scope() as session:
            custom = await get_command_messages(session, settings.target_guild_id)
        return render_message(custom, key, **kwargs)

    @app_commands.command(name="ping", description="Check that Olisar is alive.")
    async def ping(self, interaction: discord.Interaction) -> None:
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(
            await self._msg("ping", latency=latency_ms), ephemeral=True
        )

    @app_commands.command(name="ask", description="Ask Olisar something.")
    @app_commands.describe(prompt="What do you want to ask?")
    async def ask(self, interaction: discord.Interaction, prompt: str) -> None:
        # Role gate before deferring, so a denied user gets a clean ephemeral notice. In a
        # DM the command borrows a real guild the bot is in (DMs use guild_id 0 for memory).
        cfg_guild = settings.target_guild_id if interaction.guild_id else dm_home_guild_id(self.bot)
        async with session_scope() as session:
            cfg = await session.get(GuildConfig, cfg_guild)
            allowed = cfg.allowed_role_ids if cfg else []
            blocked = cfg.blocked_role_ids if cfg else []
            mention_block = list(cfg.blocked_mentions or []) if cfg else []
            denied_msg = render_message(
                cfg.command_messages if cfg and cfg.command_messages else {}, "access_denied"
            )
        member = resolve_member(self.bot, interaction.user)
        if not member_allowed(member, allowed=allowed, blocked=blocked, user_id=interaction.user.id):
            await interaction.response.send_message(denied_msg, ephemeral=True)
            return

        # Defer immediately — generation can take a few seconds (and shows "thinking").
        await interaction.response.defer(thinking=True)
        guild_id = interaction.guild_id or DM_GUILD_ID
        async with session_scope() as session:
            text = await generate_reply(
                session,
                guild_id=guild_id,
                home_guild_id=cfg_guild,  # DM /ask draws features from a real guild
                channel_id=interaction.channel_id,
                current_message_id=0,  # not a stored message
                bot_user_id=self.bot.user.id,
                user_id=interaction.user.id,
                display_name=interaction.user.display_name,
                user_text=prompt,
                actions=BotActions(self.bot, channel=interaction.channel),
            )
        am = mention_policy(mention_block)
        chunks = chunk_text(sanitize_mentions(text, mention_block)) or ["…"]
        await interaction.followup.send(chunks[0], allowed_mentions=am)
        for extra in chunks[1:]:
            await interaction.followup.send(extra, allowed_mentions=am)

    @app_commands.command(
        name="catchup", description="Get caught up on what you missed in this channel."
    )
    @app_commands.describe(
        hours="Optional: how many hours back to cover (default: since you last posted)."
    )
    async def catchup(
        self, interaction: discord.Interaction, hours: int | None = None
    ) -> None:
        async with session_scope() as session:
            cfg = await session.get(GuildConfig, settings.target_guild_id)
            allowed = cfg.allowed_role_ids if cfg else []
            blocked = cfg.blocked_role_ids if cfg else []
            mention_block = list(cfg.blocked_mentions or []) if cfg else []
            denied_msg = render_message(
                cfg.command_messages if cfg and cfg.command_messages else {}, "access_denied"
            )
        member = resolve_member(self.bot, interaction.user)
        if not member_allowed(member, allowed=allowed, blocked=blocked, user_id=interaction.user.id):
            await interaction.response.send_message(denied_msg, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        guild_id = interaction.guild_id or DM_GUILD_ID
        async with session_scope() as session:
            text = await generate_catchup(
                session,
                guild_id=guild_id,
                channel_id=interaction.channel_id,
                user_id=interaction.user.id,
                hours=hours if (hours and hours > 0) else None,
            )
        am = mention_policy(mention_block)
        chunks = chunk_text(sanitize_mentions(text, mention_block)) or ["…"]
        await interaction.followup.send(chunks[0], allowed_mentions=am)
        for extra in chunks[1:]:
            await interaction.followup.send(extra, allowed_mentions=am)

    @app_commands.command(name="privacy", description="See how Olisar handles your data.")
    async def privacy(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self._msg("privacy"), ephemeral=True)

    @app_commands.command(name="forget-me", description="Delete what Olisar remembers about you.")
    @app_commands.describe(stop_remembering="Also stop recording your messages from now on.")
    async def forget_me(
        self, interaction: discord.Interaction, stop_remembering: bool = False
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with session_scope() as session:
            result = await forget_user(
                session,
                guild_ids=[settings.target_guild_id, DM_GUILD_ID],
                user_id=interaction.user.id,
                opt_out=stop_remembering,
            )
        msg = await self._msg("forget_me", messages=result["messages"], facts=result["facts"])
        if stop_remembering:
            msg += " " + await self._msg("forget_me_optout")
        await interaction.followup.send(msg, ephemeral=True)

    # Admin-only group (only visible to members with Manage Server).
    olisar = app_commands.Group(
        name="olisar",
        description="Configure Olisar in this server.",
        default_permissions=discord.Permissions(manage_guild=True),
        guild_only=True,
    )

    @olisar.command(name="watch", description="Have Olisar read & remember this channel.")
    async def watch(self, interaction: discord.Interaction) -> None:
        async with session_scope() as session:
            row = await session.scalar(
                select(ChannelAllowlist).where(
                    ChannelAllowlist.guild_id == interaction.guild_id,
                    ChannelAllowlist.channel_id == interaction.channel_id,
                )
            )
            if row is None:
                session.add(
                    ChannelAllowlist(
                        guild_id=interaction.guild_id,
                        channel_id=interaction.channel_id,
                        mode=ChannelMode.both,
                        added_by=interaction.user.id,
                    )
                )
            else:
                row.mode = ChannelMode.both
        await interaction.response.send_message(await self._msg("watch"), ephemeral=True)

    @olisar.command(name="unwatch", description="Stop Olisar reading this channel.")
    async def unwatch(self, interaction: discord.Interaction) -> None:
        async with session_scope() as session:
            row = await session.scalar(
                select(ChannelAllowlist).where(
                    ChannelAllowlist.guild_id == interaction.guild_id,
                    ChannelAllowlist.channel_id == interaction.channel_id,
                )
            )
            if row is not None:
                row.mode = ChannelMode.off
        await interaction.response.send_message(await self._msg("unwatch"), ephemeral=True)

    @olisar.command(name="status", description="Show how Olisar is set up in this channel.")
    async def status(self, interaction: discord.Interaction) -> None:
        async with session_scope() as session:
            row = await session.scalar(
                select(ChannelAllowlist).where(
                    ChannelAllowlist.guild_id == interaction.guild_id,
                    ChannelAllowlist.channel_id == interaction.channel_id,
                )
            )
            mode = row.mode.value if row else "off"
        await interaction.response.send_message(
            await self._msg("channel_status", mode=mode), ephemeral=True
        )

    # ── Knowledge base ──────────────────────────────────────────────────
    @olisar.command(name="learn-url", description="Teach Olisar a single web page.")
    @app_commands.describe(url="The page URL to ingest.")
    async def learn_url(self, interaction: discord.Interaction, url: str) -> None:
        await interaction.response.defer(ephemeral=True)
        if not url.lower().startswith(("http://", "https://")):
            await interaction.followup.send("please give a full http(s) URL.", ephemeral=True)
            return
        async with session_scope() as session:
            session.add(
                KBSource(
                    guild_id=settings.target_guild_id,
                    type=KBSourceType.url,
                    uri=url,
                    title=url,
                    status=KBStatus.pending,
                    added_by=interaction.user.id,
                )
            )
        await interaction.followup.send(await self._msg("learn_url", url=url), ephemeral=True)

    @olisar.command(name="learn-site", description="Crawl a whole website into Olisar's knowledge.")
    @app_commands.describe(
        url="Starting URL.",
        depth="Link-hops to follow, 0–3 (default 1).",
        max_pages="Max pages, up to 100 (default 25).",
    )
    async def learn_site(
        self, interaction: discord.Interaction, url: str, depth: int = 1, max_pages: int = 25
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if not url.lower().startswith(("http://", "https://")):
            await interaction.followup.send("please give a full http(s) URL.", ephemeral=True)
            return
        depth = max(0, min(depth, 3))
        max_pages = max(1, min(max_pages, 100))
        async with session_scope() as session:
            session.add(
                KBSource(
                    guild_id=settings.target_guild_id,
                    type=KBSourceType.website,
                    uri=url,
                    title=url,
                    status=KBStatus.pending,
                    crawl_depth=depth,
                    max_pages=max_pages,
                    added_by=interaction.user.id,
                )
            )
        await interaction.followup.send(
            await self._msg("learn_site", url=url, depth=depth, max_pages=max_pages),
            ephemeral=True,
        )

    @olisar.command(name="learn-doc", description="Upload a document (PDF/DOCX/TXT/MD) to learn.")
    @app_commands.describe(file="The document to ingest.")
    async def learn_doc(self, interaction: discord.Interaction, file: discord.Attachment) -> None:
        await interaction.response.defer(ephemeral=True)
        suffix = Path(file.filename).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            await interaction.followup.send(
                f"unsupported type `{suffix}` — i can read pdf, docx, txt, md.", ephemeral=True
            )
            return
        if file.size > MAX_DOC_BYTES:
            await interaction.followup.send("that file's too big (max 10 MB).", ephemeral=True)
            return
        KB_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", file.filename)
        dest = KB_UPLOAD_DIR / f"{interaction.id}_{safe_name}"
        await file.save(dest)
        async with session_scope() as session:
            session.add(
                KBSource(
                    guild_id=settings.target_guild_id,
                    type=KBSourceType.doc,
                    uri=str(dest),
                    title=file.filename,
                    status=KBStatus.pending,
                    added_by=interaction.user.id,
                )
            )
        await interaction.followup.send(
            await self._msg("learn_doc", filename=file.filename), ephemeral=True
        )

    @olisar.command(name="sources", description="List Olisar's knowledge-base sources.")
    async def sources(self, interaction: discord.Interaction) -> None:
        async with session_scope() as session:
            rows = (
                await session.scalars(
                    select(KBSource)
                    .where(KBSource.guild_id == settings.target_guild_id)
                    .order_by(KBSource.id)
                )
            ).all()
        if not rows:
            await interaction.response.send_message(
                "knowledge base is empty — add with `/olisar learn-url`, `learn-site`, or `learn-doc`.",
                ephemeral=True,
            )
            return
        lines = [
            f"`{r.id}` [{r.status.value}] {r.type.value} — {r.title or r.uri}"
            + (f"  ⚠️ {r.error}" if r.error else "")
            for r in rows[:25]
        ]
        await interaction.response.send_message(
            "**Knowledge sources:**\n" + "\n".join(lines), ephemeral=True
        )

    @olisar.command(name="forget-source", description="Remove a knowledge-base source by id.")
    @app_commands.describe(source_id="The id shown by /olisar sources.")
    async def forget_source(self, interaction: discord.Interaction, source_id: int) -> None:
        await interaction.response.defer(ephemeral=True)
        async with session_scope() as session:
            src = await session.get(KBSource, source_id)
            if src is None or src.guild_id != settings.target_guild_id:
                await interaction.followup.send("no source with that id.", ephemeral=True)
                return
            chunk_ids = (
                await session.scalars(select(KBChunk.id).where(KBChunk.source_id == source_id))
            ).all()
            for cid in chunk_ids:
                await delete_embedding(session, "kb_chunk_embedding", cid)
            title = src.title or src.uri
            await session.delete(src)  # cascades to kb_chunk rows
        await interaction.followup.send(
            f"🗑️ removed **{title}** and its {len(chunk_ids)} chunks.", ephemeral=True
        )

    # ── Proactivity ─────────────────────────────────────────────────────
    @olisar.command(name="proactive", description="Configure whether Olisar chimes in unprompted.")
    @app_commands.describe(
        enabled="Turn proactive chiming on or off.",
        level="How eager: low = rare/high-confidence, high = chatty.",
    )
    @app_commands.choices(
        level=[
            app_commands.Choice(name="low (rare, high-confidence)", value="low"),
            app_commands.Choice(name="medium (balanced)", value="med"),
            app_commands.Choice(name="high (chatty)", value="high"),
        ]
    )
    async def proactive(
        self,
        interaction: discord.Interaction,
        enabled: bool,
        level: app_commands.Choice[str] | None = None,
    ) -> None:
        async with session_scope() as session:
            pconf = await session.get(ProactivityConfig, settings.target_guild_id)
            if pconf is None:
                pconf = ProactivityConfig(guild_id=settings.target_guild_id)
                session.add(pconf)
            pconf.enabled = enabled
            if level is not None:
                pconf.level = ProactivityLevel(level.value)
            current_level = pconf.level.value
        state = "on" if enabled else "off"
        await interaction.response.send_message(
            await self._msg("proactive", state=state, level=current_level), ephemeral=True
        )

    # ── Search index ────────────────────────────────────────────────────
    @olisar.command(
        name="reindex",
        description="(Re)build the server-wide message search index from history.",
    )
    async def reindex(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        async with session_scope() as session:
            await session.execute(
                update(GuildChannelInfo)
                .where(GuildChannelInfo.guild_id == interaction.guild_id)
                .values(backfill_done=False, last_indexed_message_id=None)
            )
            channels = await session.scalar(
                select(func.count())
                .select_from(GuildChannelInfo)
                .where(GuildChannelInfo.guild_id == interaction.guild_id)
            )
            indexed = await session.scalar(
                select(func.count())
                .select_from(SearchMessage)
                .where(SearchMessage.guild_id == interaction.guild_id)
            )
        await interaction.followup.send(
            f"queued **{channels or 0}** channels for history backfill — i'll crawl "
            f"them in the background (only channels i can read). currently indexing "
            f"**{indexed or 0}** messages; new posts are indexed live.",
            ephemeral=True,
        )

    @olisar.command(
        name="clear-index",
        description="Wipe the server-wide message search index (new posts still index live).",
    )
    async def clear_index(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        async with session_scope() as session:
            removed = await clear_search_index(session, interaction.guild_id)
        await interaction.followup.send(
            f"cleared the search index — removed **{removed}** indexed message(s). backfill "
            f"is halted; new posts are still indexed live, and `/olisar reindex` rebuilds history.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Slash(bot))
