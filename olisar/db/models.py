"""SQLAlchemy ORM models — the single source of truth for Olisar's schema.

Read this alongside the plan's "Data model" section. A few conventions:

* Discord IDs (snowflakes) are 64-bit, so they're stored as ``BigInteger``.
* Vector data is NOT defined here. ``sqlite-vec`` exposes vectors through
  ``vec0`` *virtual* tables, which don't fit SQLAlchemy's ORM. They are created
  in ``olisar/memory/vectors.py`` and keyed by the ``rowid`` of the parent row
  below (e.g. ``message_embedding.rowid == message.id``). Deleting a parent row
  and pruning its vector row happen together in the same transaction.
* Admin-editable behaviour lives in ``guild_config`` / ``persona`` /
  ``proactivity_config`` so the dashboard can change it live (see plan §8).
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    """Timezone-aware UTC now (``datetime.utcnow`` is deprecated in 3.12+)."""
    return datetime.now(timezone.utc)


# Discord epoch (2015-01-01) in ms — snowflake ids encode the creation time.
DISCORD_EPOCH_MS = 1420070400000


def snowflake_time(message_id: int) -> datetime:
    """The real UTC creation time encoded in a Discord snowflake id. Lets us date
    a message by its id alone — accurate even for history backfilled long after
    the message was posted (when wall-clock 'now' would be wrong)."""
    return datetime.fromtimestamp(
        ((message_id >> 22) + DISCORD_EPOCH_MS) / 1000, tz=timezone.utc
    )


class Base(DeclarativeBase):
    pass


# ─── Enums ──────────────────────────────────────────────────────────────────


class ChannelMode(str, enum.Enum):
    off = "off"          # Olisar ignores this channel entirely
    memory = "memory"    # read & remember, but never speak here
    respond = "respond"  # may speak here, but don't store history
    both = "both"        # read, remember, and may speak
    # Context-only channels (fed in as background, never conversational):
    resource = "resource"  # durable reference (e.g. #rules, #roles-list) — always in context
    feed = "feed"          # ambient stream (e.g. #announcements) — last few msgs, no summary


# Modes whose content Olisar pulls in as background context rather than chatting.
CONTEXT_MODES = (ChannelMode.resource, ChannelMode.feed)


class KBSourceType(str, enum.Enum):
    doc = "doc"
    url = "url"          # a single page
    website = "website"  # crawl multiple pages from one origin


class KBStatus(str, enum.Enum):
    pending = "pending"
    crawling = "crawling"
    chunking = "chunking"
    ready = "ready"
    error = "error"


class UserMemoryKind(str, enum.Enum):
    fact = "fact"
    preference = "preference"
    event = "event"


class ProactivityLevel(str, enum.Enum):
    off = "off"
    low = "low"
    med = "med"
    high = "high"


class AdminGrant(str, enum.Enum):
    allowlist = "allowlist"
    manage_guild = "manage_guild"


# ─── Guild + configuration ───────────────────────────────────────────────────


class Guild(Base):
    __tablename__ = "guild"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Discord guild id
    name: Mapped[str] = mapped_column(String(128), default="")
    icon: Mapped[str] = mapped_column(String(256), default="")  # icon URL, for the dashboard switcher
    active: Mapped[bool] = mapped_column(Boolean, default=True)  # False once the bot is removed
    privacy_notice_ack: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    config: Mapped["GuildConfig"] = relationship(back_populates="guild", uselist=False)
    persona: Mapped["Persona"] = relationship(back_populates="guild", uselist=False)
    proactivity: Mapped["ProactivityConfig"] = relationship(
        back_populates="guild", uselist=False
    )


class GuildConfig(Base):
    __tablename__ = "guild_config"

    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guild.id", ondelete="CASCADE"), primary_key=True
    )
    # Words that, at the start of a message, count as addressing Olisar directly.
    name_triggers: Mapped[list] = mapped_column(JSON, default=lambda: ["olisar"])
    reply_in_dms: Mapped[bool] = mapped_column(Boolean, default=True)
    # Whether Olisar may respond to messages that aren't an explicit trigger
    # (the looser "join the conversation" behaviour, separate from proactivity).
    loose_msg_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Role-based access control (ids stored as strings for JSON snowflake safety).
    # Empty/NULL on both = open to everyone. If allowed_role_ids is non-empty, only
    # members with one of those roles may use Olisar (chat + slash); blocked_role_ids
    # always denies. Server admins (Manage Server) bypass both. See olisar/access.py.
    allowed_role_ids: Mapped[list] = mapped_column(JSON, default=list)
    blocked_role_ids: Mapped[list] = mapped_column(JSON, default=list)
    default_model: Mapped[str] = mapped_column(String(64), default="gemini-flash-latest")
    grounding_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    grounding_daily_cap: Mapped[int] = mapped_column(Integer, default=100)
    # When a channel accumulates this many unsummarized tokens, roll a summary.
    summary_token_threshold: Mapped[int] = mapped_column(Integer, default=4000)
    # Mine the guild glossary once a channel has this many un-mined tokens — runs
    # independently of (and far more often than) summarization, so lore accrues fast.
    glossary_mine_token_threshold: Mapped[int] = mapped_column(Integer, default=1500)
    # Regenerate a user's persona after this many new messages from them.
    user_persona_msg_threshold: Mapped[int] = mapped_column(Integer, default=15)
    # Situational-awareness tools (get_user_status / who_is_in_voice) read members'
    # live Discord presence — privileged + sensitive, so opt-in per server and
    # disclosed in /privacy. Off by default.
    presence_tools_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Deprecated: the rate-limit reply now lives in command_messages["rate_limit"]
    # (editable under Command replies). Column kept to avoid a destructive migration.
    rate_limit_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Admin overrides for slash-command reply text (key -> template string).
    # Empty/NULL falls back to the defaults in olisar/messages.py.
    command_messages: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    # Bumped on every config save (reserved for future cache invalidation).
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    guild: Mapped[Guild] = relationship(back_populates="config")


class Persona(Base):
    __tablename__ = "persona"

    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guild.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(64), default="Olisar")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    tone_notes: Mapped[str] = mapped_column(Text, default="")
    # Desired profile bio text. Discord won't let a bot set this at runtime, so
    # the dashboard surfaces it as copy-paste for the Developer Portal.
    desired_bio: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    guild: Mapped[Guild] = relationship(back_populates="persona")


class ChannelAllowlist(Base):
    __tablename__ = "channel_allowlist"
    __table_args__ = (UniqueConstraint("guild_id", "channel_id", name="uq_channel_per_guild"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guild.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    mode: Mapped[ChannelMode] = mapped_column(Enum(ChannelMode), default=ChannelMode.both)
    # Rolling-summary bookkeeping (plan §"Rolling channel memory").
    unsummarized_tokens: Mapped[int] = mapped_column(Integer, default=0)
    last_summary_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    added_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChannelContextItem(Base):
    """A snapshot message from a context-only channel (mode ``resource`` or
    ``feed``). The context-channels worker keeps this in sync with Discord:
    resource channels hold a rolling reference snapshot; feed channels hold just
    the last few messages. Never summarized or embedded — injected verbatim as
    background context (and resource content also informs persona synthesis)."""

    __tablename__ = "channel_context_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    channel_name: Mapped[str] = mapped_column(String(128), default="")
    author_name: Mapped[str] = mapped_column(String(128), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    # Discord message id + timestamp, so the worker can order + de-dupe.
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GuildChannelInfo(Base):
    """Lightweight roster of the guild's text channels, synced by the bot so the
    dashboard can offer a real channel picker (the API process has no gateway)."""

    __tablename__ = "guild_channel_info"

    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # snowflake
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    category: Mapped[str] = mapped_column(String(128), default="")
    topic: Mapped[str] = mapped_column(String(1024), default="")  # channel topic/description
    index_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # in the all-channel search index?
    position: Mapped[int] = mapped_column(Integer, default=0)
    # "text" | "forum" | "thread". Forums are configurable in the dashboard; threads
    # are rostered only so the backfill can index them (hidden from the picker —
    # they inherit their parent's mode at runtime). parent_id is set for threads.
    kind: Mapped[str] = mapped_column(String(16), default="text")
    parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Oldest Discord message id the search backfill has reached in this channel
    # (NULL = not yet backfilled). The reindex worker pages history older than this.
    last_indexed_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Set once history is exhausted, so the backfill stops paging this channel.
    backfill_done: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class GuildRole(Base):
    """Roster of the guild's roles, synced by the bot so the dashboard's access
    picker can list real roles (the API process has no gateway). Mirrors
    GuildChannelInfo; @everyone is excluded by the sync."""

    __tablename__ = "guild_role"

    role_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # snowflake
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    color: Mapped[str] = mapped_column(String(16), default="")  # hex, e.g. #5865F2
    position: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ─── Conversation memory ───────────────────────────────────────────────────────


class Message(Base):
    __tablename__ = "message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # local PK (= vec rowid)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)  # Discord id
    author_id: Mapped[int] = mapped_column(BigInteger, index=True)
    author_is_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    content: Mapped[str] = mapped_column(Text, default="")
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    embedded: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # Set once this message has been folded into a channel_summary (prune-eligible).
    summarized: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # Set once mined for guild glossary facts (independent of summarization).
    fact_mined: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class ChannelSummary(Base):
    """Durable, distilled memory for a channel — see plan's rolling-summary flow."""

    __tablename__ = "channel_summary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # = vec rowid
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    summary: Mapped[str] = mapped_column(Text)
    covers_from_msg: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    covers_to_msg: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    embedded: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


# ─── Per-user memory ───────────────────────────────────────────────────────────


class UserProfile(Base):
    __tablename__ = "user_profile"
    __table_args__ = (UniqueConstraint("user_id", "guild_id", name="uq_user_per_guild"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    # Snapshot of the member's Discord roles, kept fresh by the members cog.
    # Stored as [{"id": "<snowflake-as-str>", "name": "..."}] — strings so JSON
    # consumers (the dashboard) don't lose precision on 64-bit ids.
    roles: Mapped[list] = mapped_column(JSON, default=list)
    memory_opt_out: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[dict] = mapped_column(JSON, default=dict)
    # A generated characterization of this user, synthesized from their message
    # history (Phase 2). Distinct from Olisar's own persona; lets Olisar tailor
    # how it talks to each person. Regenerated when `messages_since_persona`
    # crosses guild_config.user_persona_msg_threshold. Cleared on opt-out/forget.
    persona_summary: Mapped[str] = mapped_column(Text, default="")
    persona_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    messages_since_persona: Mapped[int] = mapped_column(Integer, default=0)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class UserMemory(Base):
    __tablename__ = "user_memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # = vec rowid
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    kind: Mapped[UserMemoryKind] = mapped_column(Enum(UserMemoryKind), default=UserMemoryKind.fact)
    content: Mapped[str] = mapped_column(Text)
    salience: Mapped[float] = mapped_column(Float, default=0.5)
    source_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # For kind='event': when the thing happens / a follow-up should fire (drives the
    # auto-reminder created alongside the fact).
    event_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    embedded: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Reminder(Base):
    """A scheduled nudge — set by a member ('remind me in 2h…') or created
    automatically from a time-bound fact. A 30s dispatch loop fires the ones whose
    time has come (see bot/cogs/reminders.py)."""

    __tablename__ = "reminder"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, default=0)  # where it was set / posts
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    target: Mapped[str] = mapped_column(String(8), default="dm")  # "dm" | "channel"
    content: Mapped[str] = mapped_column(Text)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(String(16), default="user")  # "user" | "event_fact"
    fired: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ─── Server-wide search index ───────────────────────────────────────────────────


class SearchMessage(Base):
    """Every message from every channel — a flat, keyword-searchable index that
    powers the ``search_messages`` tool ("what's the server's X account?").

    Deliberately SEPARATE from the conversational ``message`` table: this captures
    all channels (including ``off`` ones), per the admin's explicit choice, and is
    used ONLY for on-demand search — never for recall, summaries, or personas, so
    that the channel-mode opt-in still governs ambient behaviour. A companion FTS5
    table ``search_message_fts`` (raw DDL in olisar/memory/vectors.py) keeps an
    inverted index in lockstep via triggers; its AFTER DELETE trigger is what keeps
    ``/forget-me`` consistent — see olisar/memory/purge.py.
    """

    __tablename__ = "search_message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # = fts rowid
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    channel_name: Mapped[str] = mapped_column(String(128), default="")
    message_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)  # Discord id
    author_id: Mapped[int] = mapped_column(BigInteger, index=True)
    author_name: Mapped[str] = mapped_column(String(128), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


# ─── Extensions ─────────────────────────────────────────────────────────────────


class ExtensionState(Base):
    """Enabled/disabled state (and future per-extension settings) for a bot
    extension — a togglable package of extra features. The catalog of extensions
    lives in code (olisar/extensions); this table only records which are on. A
    missing row means "use the extension's default_enabled". Read live by the
    pipeline, so a dashboard toggle takes effect on the next reply."""

    __tablename__ = "extension_state"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)  # reserved for per-ext config
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ExtensionPackage(Base):
    """An SDK extension: an operator-authored (or seeded built-in) package of
    TypeScript that runs in the sandbox. ``ExtensionState`` still records per-guild
    enable/settings; this table holds the catalog entry, code, manifest, and the
    operator-approved capability allowlist. The manifest doubles as the future
    marketplace bundle. Loaded live by olisar/extensions/user_registry (cached)."""

    __tablename__ = "extension_package"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)  # == manifest.id
    name: Mapped[str] = mapped_column(String(128), default="")
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    kind: Mapped[str] = mapped_column(String(16), default="user")  # "user" | "builtin"
    category: Mapped[str] = mapped_column(String(64), default="General")
    description: Mapped[str] = mapped_column(Text, default="")
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)  # declarative spec (schema v1)
    source_ts: Mapped[str] = mapped_column(Text, default="")  # editor content (the source of truth)
    compiled_js: Mapped[str] = mapped_column(Text, default="")  # server-transpiled JS (what runs)
    # Permission split: ``requested_permissions`` is what the manifest declares (author's
    # ask); ``permissions`` is what's actually granted and enforced at runtime. For a
    # locally-authored extension the operator trusts themselves, so granted == requested;
    # for an imported/marketplace bundle the installer grants a (possibly narrower) subset.
    permissions: Mapped[list] = mapped_column(JSON, default=list)  # GRANTED (effective) capabilities
    requested_permissions: Mapped[list] = mapped_column(JSON, default=list)  # manifest-declared
    author_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Provenance — where this package came from and how to verify it (marketplace/.olx).
    origin: Mapped[str] = mapped_column(String(16), default="local")  # local | imported | marketplace
    publisher_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    publisher_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)  # "sha256:<hex>" of canonical source
    # Signed-bundle provenance: the publisher's Ed25519 signature over content_hash, the
    # public key that verifies it, and whether verification passed when it was imported.
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    publisher_key: Mapped[str | None] = mapped_column(Text, nullable=True)  # signer's public key (b64)
    signature_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Where a marketplace install came from (JSON: registry/namespace/name/version), so a
    # future update check can compare against the registry's latest. None for local/file.
    marketplace_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    sdk_version: Mapped[str] = mapped_column(String(16), default="1")  # SDK surface the code targets
    # True once an operator has edited a built-in in the console, so the seeder stops
    # overwriting it on boot (ships updates only to untouched built-ins).
    user_modified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ExtensionVersion(Base):
    """A snapshot of an extension package, written before each save so the editor
    can show history and roll back."""

    __tablename__ = "extension_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[str] = mapped_column(String(32), default="")
    source_ts: Mapped[str] = mapped_column(Text, default="")
    compiled_js: Mapped[str] = mapped_column(Text, default="")
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    saved_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExtensionKV(Base):
    """A small per-extension, per-guild key/value store exposed to sandboxed code as
    ``host.kv``. JSON values; the extension owns its namespace."""

    __tablename__ = "extension_kv"

    ext_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    k: Mapped[str] = mapped_column(String(128), primary_key=True)
    v: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON, default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AppSecret(Base):
    """App-global API keys entered from the dashboard, which override the matching
    .env value when set (blank = fall back to .env). Single row (id=1); these aren't
    per-guild. Read live (with a short cache) by the Gemini / Cloudflare / UEX call
    sites so an operator can paste their own keys without editing .env or restarting."""

    __tablename__ = "app_secret"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    gemini_api_key: Mapped[str] = mapped_column(Text, default="")
    cloudflare_account_id: Mapped[str] = mapped_column(Text, default="")
    cloudflare_api_token: Mapped[str] = mapped_column(Text, default="")
    uex_api_key: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SigningIdentity(Base):
    """This bot's Ed25519 publisher identity, used to sign the ``.olx`` bundles it
    exports. Single row (id=1), created lazily on first export. The private key never
    leaves the server and is deliberately NOT surfaced by any read endpoint; only the
    public key + fingerprint are shared (in exported bundles, so importers can verify)."""

    __tablename__ = "signing_identity"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    algo: Mapped[str] = mapped_column(String(16), default="ed25519")
    private_key: Mapped[str] = mapped_column(Text, default="")  # raw private key, base64
    public_key: Mapped[str] = mapped_column(Text, default="")  # raw public key, base64
    fingerprint: Mapped[str] = mapped_column(String(80), default="")  # "sha256:<hex>" of public key
    # Marketplace publisher registration: the namespace (handle) this bot owns and the
    # bearer token the registry issued for publishing under it. None until registered.
    registry_handle: Mapped[str | None] = mapped_column(String(64), nullable=True)
    registry_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    registry_verified: Mapped[bool] = mapped_column(Boolean, default=False)  # Discord-verified on the registry
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppConfig(Base):
    """App-global *deployment* config entered from the first-run setup wizard, which
    overrides the matching .env value when set. Single row (id=1). Lets the packaged
    desktop app be configured entirely from the UI — Discord credentials, the public
    URL, and the Tailscale Funnel — with no .env file. Read live via runtime_config;
    a developer's .env keeps working because every getter falls back to it."""

    __tablename__ = "app_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    discord_token: Mapped[str] = mapped_column(Text, default="")
    discord_client_id: Mapped[str] = mapped_column(Text, default="")
    discord_client_secret: Mapped[str] = mapped_column(Text, default="")
    target_guild_id: Mapped[int] = mapped_column(BigInteger, default=0)
    # Auto-generated on first run and persisted so it's stable across restarts
    # (regenerating would invalidate every signed session cookie).
    session_secret: Mapped[str] = mapped_column(Text, default="")
    # Set when running behind a tunnel; otherwise the loopback origin is used live.
    public_base_url: Mapped[str] = mapped_column(Text, default="")
    tunnel_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Remote access via Tailscale Funnel: tunnel_token = the Tailscale auth key;
    # tunnel_node = the chosen node name; tunnel_hostname = the resolved public
    # <node>.<tailnet>.ts.net host (used for the public URL + OAuth redirect).
    tunnel_hostname: Mapped[str] = mapped_column(Text, default="")
    tunnel_node: Mapped[str] = mapped_column(Text, default="")
    tunnel_token: Mapped[str] = mapped_column(Text, default="")
    # Desktop app: whether Olisar shows its system tray / menu-bar item. Read by the
    # Electron shell; ignored when running from source. Default on.
    show_in_menu_bar: Mapped[bool] = mapped_column(Boolean, default=True)
    # Marketplace policy: block publishing an extension whose AI risk score (0-100) is at
    # or above this. Operator-tunable; 70 is a balanced default.
    extension_risk_threshold: Mapped[int] = mapped_column(Integer, default=70)
    configured: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ─── Guild lore / glossary ─────────────────────────────────────────────────────


class GuildFact(Base):
    """Durable, server-specific lore distilled from conversations — abbreviation
    expansions, org/person relationships, codenames, in-joke meanings, and the
    like (e.g. "ICA" → "Ironclad Assault", "Griefernet is an enemy org run by
    Griefenfuhrer"). Distinct from per-user personas and the knowledge base: this
    is a compact glossary the bot always carries so it speaks the community's
    own dialect. Extracted during channel summarization (see olisar/memory/facts.py).
    """

    __tablename__ = "guild_fact"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    # The term / entity the fact is about (used as the dedup + display key).
    subject: Mapped[str] = mapped_column(String(128), default="", index=True)
    # A short, standalone factual statement about the subject.
    fact: Mapped[str] = mapped_column(Text)
    source_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Times this fact has been re-observed — used to rank the glossary by salience.
    mentions: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ─── Knowledge base ────────────────────────────────────────────────────────────


class KBSource(Base):
    __tablename__ = "kb_source"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    type: Mapped[KBSourceType] = mapped_column(Enum(KBSourceType))
    uri: Mapped[str] = mapped_column(Text)  # URL, or stored filename for docs
    title: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[KBStatus] = mapped_column(Enum(KBStatus), default=KBStatus.pending, index=True)
    crawl_depth: Mapped[int] = mapped_column(Integer, default=1)
    max_pages: Mapped[int] = mapped_column(Integer, default=50)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_ingested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    chunks: Mapped[list["KBChunk"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class KBChunk(Base):
    __tablename__ = "kb_chunk"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # = vec rowid
    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("kb_source.id", ondelete="CASCADE"), index=True
    )
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    page_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    heading_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedded: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    source: Mapped[KBSource] = relationship(back_populates="chunks")


# ─── Proactivity ───────────────────────────────────────────────────────────────


class ProactivityConfig(Base):
    __tablename__ = "proactivity_config"

    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guild.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    level: Mapped[ProactivityLevel] = mapped_column(
        Enum(ProactivityLevel), default=ProactivityLevel.low
    )
    channel_cooldown_sec: Mapped[int] = mapped_column(Integer, default=300)
    user_cooldown_sec: Mapped[int] = mapped_column(Integer, default=120)
    global_cooldown_sec: Mapped[int] = mapped_column(Integer, default=60)
    confidence_threshold: Mapped[float] = mapped_column(Float, default=0.7)
    max_per_hour: Mapped[int] = mapped_column(Integer, default=6)
    quiet_hours: Mapped[dict] = mapped_column(JSON, default=dict)  # {"start": 23, "end": 7}
    allowed_channels: Mapped[list] = mapped_column(JSON, default=list)  # [] = all allowlisted
    # Passive emoji reactions — a separate, much looser path than chiming in: skip
    # the classifier, react (emoji only, no reply) when a reaction fits. The model
    # picks the emoji and may decline; cooldown + hourly cap keep it sparse.
    reaction_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    reaction_threshold: Mapped[float] = mapped_column(Float, default=0.0)
    reaction_cooldown_sec: Mapped[int] = mapped_column(Integer, default=60)
    reaction_max_per_hour: Mapped[int] = mapped_column(Integer, default=6)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    guild: Mapped[Guild] = relationship(back_populates="proactivity")


class ProactivityState(Base):
    """Runtime cooldown / rate counters, keyed per channel."""

    __tablename__ = "proactivity_state"
    __table_args__ = (
        UniqueConstraint("guild_id", "channel_id", name="uq_proactive_state_channel"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    last_proactive_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    count_window: Mapped[dict] = mapped_column(JSON, default=dict)  # hourly counters


# ─── Admin auth + audit ──────────────────────────────────────────────────────


class AdminUser(Base):
    __tablename__ = "admin_user"

    discord_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(128), default="")
    is_allowlisted: Mapped[bool] = mapped_column(Boolean, default=False)
    granted_via: Mapped[AdminGrant] = mapped_column(Enum(AdminGrant), default=AdminGrant.allowlist)
    # Guild ids where this user has Manage Server, captured at each login. Authorizes
    # which servers they can configure (allowlisted users get every guild the bot is in).
    managed_guild_ids: Mapped[list] = mapped_column(JSON, default=list)
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Session(Base):
    __tablename__ = "session"

    sid: Mapped[str] = mapped_column(String(64), primary_key=True)
    admin_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("admin_user.discord_user_id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    csrf_secret: Mapped[str] = mapped_column(String(64), default="")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")  # admin id | "bot" | "system"
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class GeminiUsage(Base):
    """Per-day, per-model request/token accounting for the rate limiter + dashboard."""

    __tablename__ = "gemini_usage"
    __table_args__ = (UniqueConstraint("day", "model", name="uq_usage_day_model"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[datetime] = mapped_column(Date, index=True)
    model: Mapped[str] = mapped_column(String(64))
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    grounding_count: Mapped[int] = mapped_column(Integer, default=0)
