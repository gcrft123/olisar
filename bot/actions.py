"""Concrete DiscordActions for tools that touch Discord (set_status, react).

`MessageActions` is used when Olisar is replying to a real message (so it can
react to it); `BotActions` is used for /ask interactions, where there's no
message to react to.
"""

from __future__ import annotations

import io

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


def _norm(s: str) -> str:
    """Lowercase, keep only alphanumerics — so '💬│general-chat' normalizes to
    'generalchat', letting a loose reference like 'general' match a decorated name."""
    return "".join(c for c in (s or "").lower() if c.isalnum())


def _match_text_channels(guild: discord.Guild, raw: str) -> list:
    """Rank a guild's postable text channels against a loose reference — a numeric id, a
    <#id> mention, or a (possibly partial, emoji/prefix-decorated) name. Exact name wins,
    then normalized-equal, then normalized-substring ranked by prefix and brevity. Empty
    if nothing matches."""
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
    nq = _norm(raw)
    if not nq:
        return []
    normeq = [c for c in chans if _norm(c.name) == nq]
    if normeq:
        return normeq
    contains = [c for c in chans if nq in _norm(c.name)]
    contains.sort(key=lambda c: (not _norm(c.name).startswith(nq), len(c.name)))
    return contains


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
        # Pick a confident winner, or ask when several partial matches are equally plausible.
        low, nq = ref.lower(), _norm(ref)
        exacts = [m for m in matches if m.name.lower() == low or _norm(m.name) == nq]
        prefixes = [m for m in matches if _norm(m.name).startswith(nq)]
        if len(exacts) == 1:
            target = exacts[0]
        elif not exacts and len(prefixes) == 1:
            target = prefixes[0]
        elif not exacts and len(matches) == 1:
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
