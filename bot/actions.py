"""Concrete DiscordActions for tools that touch Discord (set_status, react).

`MessageActions` is used when Olisar is replying to a real message (so it can
react to it); `BotActions` is used for /ask interactions, where there's no
message to react to.
"""

from __future__ import annotations

import io
import re

import discord

from bot.replies import chunk_text, mention_policy, sanitize_mentions

_ACTIVITY_VERB = {
    discord.ActivityType.playing: "playing",
    discord.ActivityType.streaming: "streaming",
    discord.ActivityType.listening: "listening to",
    discord.ActivityType.watching: "watching",
    discord.ActivityType.competing: "competing in",
}


def _resolve_member(guild: discord.Guild, query: str) -> discord.Member | None:
    """Find a member by numeric id (or <@id> mention) or by display/user name —
    exact match first, then a unique-ish prefix."""
    query = (query or "").strip()
    if not query:
        return None
    digits = "".join(c for c in query if c.isdigit())
    if digits:
        m = guild.get_member(int(digits))
        if m is not None:
            return m
    low = query.lstrip("@").lower()
    for m in guild.members:
        if m.display_name.lower() == low or m.name.lower() == low:
            return m
    for m in guild.members:
        if m.display_name.lower().startswith(low) or m.name.lower().startswith(low):
            return m
    return None


# Structural words people wrap a channel reference in ("the moderator channel", "send to
# X", "#moderator with an emoji"). Dropped when forming the "core" query so the meaningful
# words survive. Safe to be generous: the un-stripped form is always tried too.
_CHANNEL_FILLER = {
    "the", "a", "an", "this", "that", "my", "our", "your", "please", "send", "sends",
    "post", "posts", "message", "msg", "in", "into", "to", "named", "name", "called",
    "with", "emoji", "server", "channel", "channels", "chan",
}


def _norm(s: str) -> str:
    """Lowercase, keep only alphanumerics — so '💬│general-chat' normalizes to
    'generalchat', letting a loose reference like 'general' match a decorated name."""
    return "".join(c for c in (s or "").lower() if c.isalnum())


def _significant_tokens(s: str) -> list[str]:
    """The meaningful words of a loose reference — alphanumeric tokens minus filler."""
    return [t for t in re.findall(r"[0-9a-z]+", (s or "").lower()) if t not in _CHANNEL_FILLER]


def _query_forms(raw: str) -> list[str]:
    """Normalized query forms to match against, deduped: the whole thing, and just the
    meaningful words. 'moderator channel' / 'moderator with an emoji' both yield
    'moderator', so a decorated '🔨┃moderator' matches even with extra words around it."""
    forms: list[str] = []
    for f in (_norm(raw), "".join(_significant_tokens(raw))):
        if f and f not in forms:
            forms.append(f)
    return forms


def _match_text_channels(guild: discord.Guild, raw: str) -> list:
    """Rank a guild's postable text channels against a loose reference — a numeric id, a
    <#id> mention, or a (possibly partial, emoji/prefix/filler-decorated) name. Order:
    exact name, normalized-equal (either query form), then bidirectional normalized-
    substring (query inside a name beats a name inside the query), then a last-resort
    word-prefix pass ('mods' → moderator). Empty if nothing matches."""
    raw = (raw or "").strip().strip("<#>").lstrip("#").strip()
    if not raw:
        return []
    if raw.isdigit():
        ch = guild.get_channel(int(raw))
        return [ch] if ch is not None and hasattr(ch, "send") else []
    chans = list(guild.text_channels)
    low = raw.lower()
    exact = [c for c in chans if c.name.lower() == low]
    if exact:
        return exact
    forms = _query_forms(raw)
    if not forms:
        return []
    normeq = [c for c in chans if _norm(c.name) in forms]
    if normeq:
        return normeq
    scored = []
    for c in chans:
        cn = _norm(c.name)
        if not cn:
            continue
        best = None
        for f in forms:
            if f in cn:                       # the query sits inside the channel name
                cand = (0, abs(len(cn) - len(f)))
            elif len(cn) >= 3 and cn in f:    # the channel name sits inside the query
                cand = (1, abs(len(cn) - len(f)))
            else:
                continue
            if best is None or cand < best:
                best = cand
        if best is not None:
            scored.append((best[0], best[1], len(c.name), c))
    if scored:
        scored.sort(key=lambda x: (x[0], x[1], x[2]))
        return [c for *_, c in scored]
    toks = [t for t in _significant_tokens(raw) if len(t) >= 3]
    if toks:
        hits = []
        for c in chans:
            cn = _norm(c.name)
            n = sum(1 for t in toks if cn.startswith(t))
            if n:
                hits.append((-n, len(c.name), c))
        if hits:
            hits.sort(key=lambda x: (x[0], x[1]))
            return [c for *_, c in hits]
    return []


def _format_activities(member: discord.Member) -> str:
    bits: list[str] = []
    for a in member.activities:
        if isinstance(a, discord.CustomActivity):
            if a.name:
                bits.append(f'custom status "{a.name}"')
        elif isinstance(a, discord.Spotify):
            bits.append(f"listening to {a.title} by {a.artist}")
        else:
            verb = _ACTIVITY_VERB.get(getattr(a, "type", None), "")
            name = getattr(a, "name", None)
            if name:
                bits.append(f"{verb} {name}".strip())
    return "; ".join(bits)


class BotActions:
    """Bot-level actions available everywhere (no triggering message).

    ``channel`` is where generated images are posted; supply the interaction/
    message channel so image generation has somewhere to land (None disables it).
    """

    def __init__(
        self, bot: discord.Client, channel: discord.abc.Messageable | None = None
    ) -> None:
        self.bot = bot
        self.channel = channel

    async def set_status(self, text: str) -> str:
        try:
            await self.bot.change_presence(
                activity=discord.CustomActivity(name=text or "…")
            )
            return f"status set to: {text}"
        except Exception as exc:  # noqa: BLE001 - surfaced back to the model
            return f"couldn't set status: {exc}"

    async def react(self, emoji: str) -> str:
        return "no message to react to here"

    async def send_dm(self, user_id: int, text: str) -> str:
        """Send a private DM to a user by their numeric id. Olisar may do this on
        its own initiative; it degrades gracefully if the user has DMs closed."""
        text = (text or "").strip()
        if not text:
            return "no message to send"
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return f"'{user_id}' isn't a valid user id"
        try:
            user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
        except discord.NotFound:
            return f"no user with id {uid}"
        except Exception as exc:  # noqa: BLE001
            return f"couldn't find user {uid}: {exc}"
        if user is None:
            return f"no user with id {uid}"
        try:
            for chunk in chunk_text(text):
                await user.send(chunk)
            return f"sent a DM to {user.display_name}"
        except discord.Forbidden:
            return f"can't DM {user.display_name} — their DMs are closed to me"
        except Exception as exc:  # noqa: BLE001
            return f"couldn't DM {user.display_name}: {exc}"

    async def send_channel(
        self, channel: object, text: str, *, home_guild_id: int, requester_id: int,
        blocked_mentions: list | None = None,
    ) -> str:
        """Post plain text to a named channel on a user's behalf — the backing action for
        the send_to_channel tool. Resolves the channel loosely (name/id/#mention) within
        the home guild, and only posts when the *requester* may post there themselves, so
        nobody can use Olisar to reach a channel they're locked out of.

        @everyone/@here/role pings only fire when the requester is a server admin (Manage
        Server) AND the server's mention policy (``blocked_mentions``) allows that type —
        otherwise those mentions are neutralised (shown, but they don't ping). Non-admins
        never mass-ping through Olisar. Ordinary user mentions always pass through."""
        text = (text or "").strip()
        if not text:
            return "there's no message to send"
        guild = self.bot.get_guild(int(home_guild_id)) if home_guild_id else None
        if guild is None:
            return "I can't tell which server to post in from here."
        ref = str(channel or "").strip().strip("<#>").lstrip("#").strip()
        matches = _match_text_channels(guild, ref)
        if not matches:
            return f"I couldn't find a channel matching {ref or channel!r} in {guild.name}."
        # _match_text_channels ranks best-first. Post to it when there's a single hit or one
        # clear normalized-name winner; otherwise ask which, so we never post to the wrong one.
        forms = _query_forms(ref)
        normeq = [m for m in matches if _norm(m.name) in forms]
        if len(normeq) == 1:
            target = normeq[0]
        elif len(matches) == 1:
            target = matches[0]
        else:
            names = ", ".join("#" + m.name for m in matches[:6])
            return f"a few channels could match {ref!r} — which did you mean? {names}"
        try:
            member = guild.get_member(int(requester_id)) if requester_id else None
        except (TypeError, ValueError):
            member = None
        if member is None and requester_id:
            try:
                member = await guild.fetch_member(int(requester_id))
            except Exception:  # noqa: BLE001
                member = None
        if member is None:
            return "I can only post on behalf of someone who's a member of that server."
        uperms = target.permissions_for(member)
        if not (uperms.view_channel and uperms.send_messages):
            return (
                f"you don't have permission to post in #{target.name}, "
                "so I won't do it for you."
            )
        bperms = target.permissions_for(guild.me)
        if not (bperms.view_channel and bperms.send_messages):
            return f"I don't have permission to post in #{target.name}."
        # Mass mentions are admin-only, and then only for types the server allows. For a
        # non-admin we block every type; for an admin we honour the guild's blocked_mentions
        # exactly as Olisar's own replies do (sanitize neutralises @everyone/@here text,
        # mention_policy gates roles). Ordinary user pings always pass.
        is_admin = bool(getattr(getattr(member, "guild_permissions", None), "manage_guild", False))
        effective_blocked = (
            list(blocked_mentions or []) if is_admin else ["everyone", "here", "roles"]
        )
        body = sanitize_mentions(text, effective_blocked)
        mentions = mention_policy(effective_blocked)
        try:
            for chunk in chunk_text(body):
                await target.send(chunk, allowed_mentions=mentions)
        except discord.Forbidden:
            return f"I don't have permission to post in #{target.name}."
        except Exception as exc:  # noqa: BLE001
            return f"couldn't post in #{target.name}: {exc}"
        return f"Posted your message in #{target.name}."

    async def user_status(self, query: str, guild_id: int) -> str:
        """A member's live presence (status + current game/app), for the
        situational-awareness tool. Read on demand, never stored."""
        guild = self.bot.get_guild(int(guild_id)) if guild_id else None
        if guild is None:
            return "I can't see that server right now."
        member = _resolve_member(guild, query)
        if member is None:
            return f"I couldn't find anyone matching {query!r} here."
        status = getattr(member.status, "name", None) or "unknown"
        acts = _format_activities(member)
        base = f"{member.display_name} is {status}"
        return f"{base}; {acts}." if acts else f"{base}, with nothing showing as an activity."

    async def who_in_voice(self, guild_id: int) -> str:
        """Who is in each voice channel right now."""
        guild = self.bot.get_guild(int(guild_id)) if guild_id else None
        if guild is None:
            return "I can't see that server right now."
        lines: list[str] = []
        for vc in guild.voice_channels:
            names = [m.display_name for m in vc.members]
            if names:
                lines.append(f"{vc.name}: " + ", ".join(names))
        if not lines:
            return "Nobody's in a voice channel right now."
        return "In voice right now —\n" + "\n".join(lines)

    async def is_admin(self, user_id: int, guild_id: int) -> bool:
        """Whether ``user_id`` is a server admin (Manage Server) in ``guild_id`` — the same
        bar the access layer uses (bot/access.py). False when the guild or member can't be
        resolved (e.g. a DM from a non-member)."""
        guild = self.bot.get_guild(int(guild_id)) if guild_id else None
        if guild is None:
            return False
        member = guild.get_member(int(user_id))
        if member is None:
            try:
                member = await guild.fetch_member(int(user_id))
            except Exception:  # noqa: BLE001
                return False
        perms = getattr(member, "guild_permissions", None)
        return bool(perms and perms.manage_guild)

    async def channel_directory(
        self, guild_id: int, *, requester_id: int = 0, limit: int = 80
    ) -> str:
        """A compact 'name (id …)' listing of the guild's text channels, injected into
        context so the model can map a loose reference to the real channel + id itself —
        the same trick people_directory uses for users, and far more robust than string
        matching. Scoped to channels both the bot and (when resolvable) the requester can
        see, so it never reveals a private channel the asker can't access. '' if none."""
        guild = self.bot.get_guild(int(guild_id)) if guild_id else None
        if guild is None:
            return ""
        me = guild.me
        member = guild.get_member(int(requester_id)) if requester_id else None

        def _visible(c) -> bool:
            if me is not None and not c.permissions_for(me).view_channel:
                return False
            if member is not None and not c.permissions_for(member).view_channel:
                return False
            return True

        chans = [c for c in guild.text_channels if _visible(c)]
        if not chans:
            return ""
        shown = chans[:limit]
        entries = ", ".join(f"#{c.name} (id {c.id})" for c in shown)
        more = "" if len(chans) <= limit else f", and {len(chans) - limit} more"
        return (
            "Channels in this server (name -> id) — to post somewhere with send_to_channel, "
            "match the user's wording to one of these and pass its id: "
            + entries + more + "."
        )

    def _resolve_channel(self, channel: object, home_guild_id: int):
        """The target channel for post_components: the current channel when ``channel`` is
        falsy, else a channel by id / <#id> mention / name within ``home_guild_id``."""
        if not channel:
            return self.channel
        raw = str(channel).strip().strip("<#>").lstrip("#")
        if raw.isdigit():
            return self.bot.get_channel(int(raw))
        guild = self.bot.get_guild(int(home_guild_id)) if home_guild_id else None
        if guild is None:
            return None
        matches = _match_text_channels(guild, raw)
        return matches[0] if matches else None

    async def post_components(
        self, *, channel: object = None, content: object = None, embed: object = None,
        components: object = None, ext_key: str = "", home_guild_id: int = 0,
        trusted: bool = False,
    ) -> str:
        """Post to a channel with an optional embed + interactive components — the host side
        of host.discord.send for an extension tool. Persistent components keep working (they
        route through the global DynamicItem template, same as a slash command's). A
        third-party (untrusted) extension's post is stripped of @mentions so it can't
        @everyone / mass-ping; built-ins keep normal mentions."""
        target = self._resolve_channel(channel, home_guild_id)
        if target is None or not hasattr(target, "send"):
            return (f"I couldn't find a channel matching {channel!r} to post in."
                    if channel else "There's no channel to post in here.")
        # Built in the cog layer (needs discord.py + the persistent-component templates).
        from bot.cogs.sdk_commands import _build_view, _to_embed
        try:
            view = _build_view(list(components or []), ext_key=ext_key)
        except Exception as exc:  # noqa: BLE001 - surfaced to the model
            return f"couldn't build the message: {exc}"
        kwargs: dict = {}
        if content:
            kwargs["content"] = str(content)[:2000]
        emb = _to_embed(embed) if embed else None
        if emb is not None:
            kwargs["embed"] = emb
        if view is not None:
            kwargs["view"] = view
        if not kwargs:
            return "nothing to post"
        if not trusted:  # third-party posts can't ping anyone
            kwargs["allowed_mentions"] = discord.AllowedMentions.none()
        where = "#" + target.name if getattr(target, "name", None) else "the channel"
        try:
            await target.send(**kwargs)
        except discord.Forbidden:
            return f"I don't have permission to post in {where}."
        except Exception as exc:  # noqa: BLE001
            return f"couldn't post in {where}: {exc}"
        return f"Posted in {where}." if getattr(target, "name", None) else "Posted it here."

    async def send_image(
        self, data: bytes, *, filename: str = "olisar.png", caption: str = ""
    ) -> str:
        """Post a generated image to the active channel (set at construction)."""
        if not data:
            return "no image to send"
        if self.channel is None:
            return "can't post an image from here"
        try:
            file = discord.File(io.BytesIO(data), filename=filename)
            await self.channel.send(content=(caption or None), file=file)
            return "image posted"
        except discord.Forbidden:
            return "can't post an image here — missing permission"
        except Exception as exc:  # noqa: BLE001
            return f"couldn't post the image: {exc}"


class MessageActions(BotActions):
    """Adds reacting to the message that triggered the reply."""

    def __init__(self, bot: discord.Client, message: discord.Message) -> None:
        super().__init__(bot, channel=message.channel)
        self.message = message

    async def react(self, emoji: str) -> str:
        emoji = (emoji or "").strip()
        if not emoji:
            return "no emoji given"
        try:
            await self.message.add_reaction(emoji)
            return f"reacted with {emoji}"
        except Exception as exc:  # noqa: BLE001
            return f"couldn't react with {emoji}: {exc}"
