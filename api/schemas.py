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
    presence_tools_enabled: bool | None = None
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
