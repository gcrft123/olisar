"""Pydantic request bodies for the admin API. All fields optional on PUTs so the
dashboard can send partial updates (only the fields the admin changed)."""

from __future__ import annotations

from pydantic import BaseModel, Field

# The refresh ceiling lives with the scheduler that enforces it. Imported rather than
# retyped: a validator and a worker disagreeing about the ceiling is a console that accepts
# a schedule the backend will never run.
from olisar.knowledge.refresh import MAX_INTERVAL_HOURS


class PersonaIn(BaseModel):
    name: str | None = None
    system_prompt: str | None = None
    tone_notes: str | None = None
    desired_bio: str | None = None


class ApiKeysIn(BaseModel):
    # Each optional + only non-empty values are written, so the dashboard can submit
    # just the keys the operator typed without clearing the others.
    gemini_api_key: str | None = None
    cloudflare_account_id: str | None = None
    cloudflare_api_token: str | None = None
    uex_api_key: str | None = None


class ExtensionAuthoringIn(BaseModel):
    # Operator-authored SDK extension. ``source_ts`` is the source of truth: the server
    # transpiles it (never trusts client-supplied JS) and derives the manifest — and thus
    # the key/tools/permissions — from the JS it produced. ``compiled_js`` is accepted but
    # ignored (kept for older clients); ``source`` is an alias for source_ts.
    source_ts: str = ""
    source: str | None = None
    compiled_js: str | None = None
    name: str | None = None


class ExtensionValidateIn(BaseModel):
    source_ts: str = ""
    source: str | None = None
    compiled_js: str | None = None


class ExtensionImportIn(BaseModel):
    # A parsed .olx document (the client reads the file and posts its JSON).
    bundle: dict


class ExtensionImportConfirmIn(BaseModel):
    bundle: dict
    # The capabilities the installing operator approved; the server enforces
    # granted ⊆ requested. Empty means "install with no capabilities granted".
    granted_permissions: list[str] = []


class MarketplaceRefIn(BaseModel):
    # Coordinates of a marketplace extension version (the bot fetches the .olx itself).
    namespace: str
    name: str
    version: str


class MarketplaceInstallIn(MarketplaceRefIn):
    granted_permissions: list[str] = []


class MarketplaceRegisterIn(BaseModel):
    handle: str  # the namespace to claim (publisher identity)


class MarketplacePublishIn(BaseModel):
    key: str  # the local extension to publish


class MarketplaceYankIn(BaseModel):
    name: str
    version: str | None = None  # omit to yank all versions


class MarketplaceUpdateIn(BaseModel):
    key: str  # an installed marketplace extension to check/preview an update for


class MarketplaceUpdateApplyIn(BaseModel):
    key: str
    granted_permissions: list[str] = []


class MarketplacePolicyIn(BaseModel):
    risk_threshold: int  # 1-100; publishing is blocked at/above this AI risk score


class ReportAttachmentIn(BaseModel):
    name: str = "attachment"
    type: str = "application/octet-stream"
    content_b64: str


class MarketplaceReportIn(BaseModel):
    namespace: str
    name: str
    version: str | None = None
    description: str = ""
    logs: str = ""  # optional bot logs the reporter chose to attach
    attachments: list[ReportAttachmentIn] = []


class FeedbackIn(BaseModel):
    category: str = "Feedback"  # Feedback | Bug report | Question
    message: str
    email: str = ""             # optional reply-to address
    logs: str = ""              # optional bot logs
    attachments: list[ReportAttachmentIn] = []


class DevModerationIn(BaseModel):
    discord_id: str
    status: str  # warn | ban | clear
    message: str = ""


class DevYankIn(BaseModel):
    namespace: str
    name: str
    version: str | None = None


class SetupTokenIn(BaseModel):
    token: str


class SetupSaveIn(BaseModel):
    # First-run wizard payload. Guild id is a string (snowflakes exceed JS's safe int).
    # Tunnel config is handled separately by /api/tunnel/enable, not here.
    discord_token: str | None = None
    discord_client_id: str | None = None
    discord_client_secret: str | None = None
    target_guild_id: str | None = None


class TunnelEnableIn(BaseModel):
    auth_key: str | None = None   # Tailscale auth key (used to join the tailnet)
    hostname: str | None = None   # desired node name (default "olisar")


class ConfigIn(BaseModel):
    # The bounds match what the console prints under each field ("3-100 . default 12").
    # Without them the UI was the only thing enforcing a range it advertised, so anything
    # that wasn't the console -- a stale tab, a script, a replayed request -- could write a
    # context window of 0 and quietly break every reply.
    name_triggers: list[str] | None = None
    reply_in_dms: bool | None = None
    default_model: str | None = None
    grounding_enabled: bool | None = None
    grounding_daily_cap: int | None = Field(None, ge=0)
    summary_token_threshold: int | None = Field(None, ge=500)
    glossary_mine_token_threshold: int | None = Field(None, ge=300)
    user_persona_msg_threshold: int | None = Field(None, ge=5)
    context_message_limit: int | None = Field(None, ge=3, le=100)
    presence_tools_enabled: bool | None = None
    # Mention types the bot may not ping: any of "everyone", "here", "roles".
    blocked_mentions: list[str] | None = None
    # Role ids as strings (snowflake precision). Empty lists = open access.
    allowed_role_ids: list[str] | None = None
    blocked_role_ids: list[str] | None = None


class ProactivityIn(BaseModel):
    enabled: bool | None = None
    level: str | None = None
    channel_cooldown_sec: int | None = Field(None, ge=0)
    user_cooldown_sec: int | None = Field(None, ge=0)
    global_cooldown_sec: int | None = Field(None, ge=0)
    # The console renders this as "0-1" with a 0.05 step; it is a probability, not a count.
    confidence_threshold: float | None = Field(None, ge=0, le=1)
    max_per_hour: int | None = Field(None, ge=0)
    quiet_hours: dict | None = None
    allowed_channels: list | None = None
    reaction_enabled: bool | None = None
    reaction_threshold: float | None = Field(None, ge=0, le=1)
    reaction_cooldown_sec: int | None = Field(None, ge=0)
    reaction_max_per_hour: int | None = Field(None, ge=0)


class ChannelModeIn(BaseModel):
    channel_id: int
    mode: str | None = None  # off | memory | respond | both | resource | feed
    indexed: bool | None = None  # in the all-channel search index?


class FactIn(BaseModel):
    subject: str | None = None
    fact: str


class SourceIn(BaseModel):
    type: str  # url | website
    uri: str
    # The router used to silently clamp these to 0-3 and 1-100, so a request asking to crawl
    # 500 pages got 100 back and was told nothing. The console already prints exactly these
    # bounds under each field; rejecting out-of-range values says what happened instead of
    # quietly doing something else.
    crawl_depth: int = Field(1, ge=0, le=3)
    max_pages: int = Field(25, ge=1, le=100)
    # How often to read this source again, in hours — 0 for never, which is what every source
    # added before this existed keeps. The floor is one hour (an int can't sit between 0 and
    # 1), the ceiling a year.
    refresh_hours: int = Field(0, ge=0, le=MAX_INTERVAL_HOURS)


class SourceScheduleIn(BaseModel):
    refresh_hours: int = Field(..., ge=0, le=MAX_INTERVAL_HOURS)


class ExtensionToggleIn(BaseModel):
    key: str
    enabled: bool


class SandboxMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class SandboxChatIn(BaseModel):
    # Full transcript so far, ending with the admin's new message. The sandbox is
    # stateless server-side (memory-free), so the client carries the history.
    messages: list[SandboxMessage]


class DesktopSettingsIn(BaseModel):
    show_in_menu_bar: bool | None = None
