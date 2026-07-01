"""Pydantic request bodies for the admin API. All fields optional on PUTs so the
dashboard can send partial updates (only the fields the admin changed)."""

from __future__ import annotations

from pydantic import BaseModel


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
    name_triggers: list[str] | None = None
    reply_in_dms: bool | None = None
    loose_msg_enabled: bool | None = None
    default_model: str | None = None
    grounding_enabled: bool | None = None
    grounding_daily_cap: int | None = None
    summary_token_threshold: int | None = None
    glossary_mine_token_threshold: int | None = None
    user_persona_msg_threshold: int | None = None
    context_message_limit: int | None = None
    presence_tools_enabled: bool | None = None
    # Mention types the bot may not ping: any of "everyone", "here", "roles".
    blocked_mentions: list[str] | None = None
    # Role ids as strings (snowflake precision). Empty lists = open access.
    allowed_role_ids: list[str] | None = None
    blocked_role_ids: list[str] | None = None


class ProactivityIn(BaseModel):
    enabled: bool | None = None
    level: str | None = None
    channel_cooldown_sec: int | None = None
    user_cooldown_sec: int | None = None
    global_cooldown_sec: int | None = None
    confidence_threshold: float | None = None
    max_per_hour: int | None = None
    quiet_hours: dict | None = None
    allowed_channels: list | None = None
    reaction_enabled: bool | None = None
    reaction_threshold: float | None = None
    reaction_cooldown_sec: int | None = None
    reaction_max_per_hour: int | None = None


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
    crawl_depth: int = 1
    max_pages: int = 25


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
