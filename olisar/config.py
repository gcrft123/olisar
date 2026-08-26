"""Process-wide settings, loaded once from the environment / `.env`.

Both the bot and the API import `settings` from here, so there is a single
source of truth for tokens, model names, paths, and service ports.

Note: this holds *deployment* config (secrets, where things live). Per-guild,
admin-editable behaviour (persona, proactivity, channel allowlist, ...) lives in
the database instead, so it can be changed from the dashboard without a restart.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from olisar.gemini.models import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_LITE_MODEL,
    DEFAULT_VISION_MODEL,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Discord ──────────────────────────────────────────────────────────
    discord_token: str = Field(default="", alias="DISCORD_TOKEN")
    discord_client_id: str = Field(default="", alias="DISCORD_CLIENT_ID")
    discord_client_secret: str = Field(default="", alias="DISCORD_CLIENT_SECRET")
    target_guild_id: int = Field(default=0, alias="TARGET_GUILD_ID")
    # Opt-in for the privileged "presences" gateway intent (needed by the
    # status/voice-awareness tools). Off by default because enabling it requires the
    # operator to ALSO turn on Presence Intent in the Discord Developer Portal — and
    # without that, the bot can't connect at all. Voice-channel awareness doesn't need
    # this (voice_states is non-privileged).
    enable_presence_intent: bool = Field(default=False, alias="OLISAR_ENABLE_PRESENCE_INTENT")

    # ── Gemini ───────────────────────────────────────────────────────────
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    # Pinned, not `-latest`: an alias moves under a running bot and the API won't say to
    # what, so a provider-side roll becomes a live incident. See olisar/gemini/models.py.
    gemini_chat_model: str = Field(default=DEFAULT_CHAT_MODEL, alias="GEMINI_CHAT_MODEL")
    gemini_lite_model: str = Field(default=DEFAULT_LITE_MODEL, alias="GEMINI_LITE_MODEL")
    # Where the vision (image-description) fallback chain starts. Defaults to a
    # mid-tier multimodal model that chat reaches last, so captioning images
    # doesn't park the top chat models. See olisar/gemini/models.IMAGE_RANKED.
    gemini_vision_model: str = Field(
        default=DEFAULT_VISION_MODEL, alias="GEMINI_VISION_MODEL"
    )

    # ── Cloudflare Workers AI (image generation) ─────────────────────────
    # Gemini's image-gen models are paid-only (free quota = 0), so image
    # generation runs on Cloudflare Workers AI's FLUX model, which has a free
    # daily allocation. Leave the account id / token blank to disable the
    # generate_image tool entirely (it then degrades gracefully).
    cloudflare_account_id: str = Field(default="", alias="CLOUDFLARE_ACCOUNT_ID")
    cloudflare_api_token: str = Field(default="", alias="CLOUDFLARE_API_TOKEN")
    cloudflare_image_model: str = Field(
        default="@cf/black-forest-labs/flux-1-schnell", alias="CLOUDFLARE_IMAGE_MODEL"
    )
    gemini_embed_model: str = Field(default="gemini-embedding-001", alias="GEMINI_EMBED_MODEL")
    # gemini-embedding-001 supports Matryoshka truncation; 768 keeps the DB small.
    embed_dim: int = Field(default=768, alias="EMBED_DIM")

    # ── Extensions ───────────────────────────────────────────────────────
    # UEX (uexcorp.space) bearer token for the Star Citizen extension. Optional:
    # most UEX read endpoints are public; a token raises limits / unlocks some.
    uex_api_key: str = Field(default="", alias="UEX_API_KEY")

    # Extension marketplace registry the console browses/installs from. Defaults to the
    # hosted Cloudflare Worker; self-hosters can point at their own registry.
    registry_url: str = Field(
        default="https://olisar-registry.gabrielyp.workers.dev",
        alias="OLISAR_REGISTRY_URL",
    )

    # ── Remote access ────────────────────────────────────────────────────
    # Tailscale auth key for Funnel-based remote access. ``.env`` fallback for the
    # ``app_config.tunnel_token`` field, so a dev can put a key in .env and skip the
    # wizard's tunnel step.
    tunnel_token: str = Field(default="", alias="TAILSCALE_AUTH")
    # Device name for the Funnel node (the ``…ts.net`` host; defaults to "olisar").
    # Also the ``.env`` fallback for ``app_config.tunnel_node``.
    tunnel_node: str = Field(default="", alias="OLISAR_FUNNEL_HOSTNAME")

    # ── Headless server deployment (the Docker image on a cloud VM) ───────
    # When set, env-supplied Discord credentials count as a completed setup (so a
    # server instance serves the dashboard instead of the loopback-only wizard), and
    # the Funnel auto-starts when ``TAILSCALE_AUTH`` is present. Off for the desktop app.
    headless: bool = Field(default=False, alias="OLISAR_HEADLESS")

    # ── Sandbox: emulated members (local testing only) ───────────────────
    # Discord bot ids that Olisar treats as ordinary members rather than bots —
    # the member-emulator fleet in ``sandbox/``. Empty in every real deployment,
    # where ``olisar.peers.is_member_author`` then reduces to "not a bot" and
    # behaviour is byte-for-byte unchanged. See olisar/peers.py.
    peer_bot_ids: Annotated[list[int], NoDecode] = Field(
        default_factory=list, alias="OLISAR_PEER_BOT_IDS"
    )

    # ── Mock auth (local dev/testing only) ───────────────────────────────
    # When set, the app counts as configured (skips onboarding) and Discord OAuth is
    # replaced by a mock consent → callback that signs you in as a mock allowlisted
    # operator on a seeded "Mock Server" — so the sign-in flow can be exercised from
    # source with no real Discord app. Never set in a real deployment.
    mock_auth: bool = Field(default=False, alias="OLISAR_MOCK_AUTH")

    # ── Admin dashboard / auth ───────────────────────────────────────────
    # NoDecode stops pydantic-settings from JSON-decoding the env value, so the
    # validator below receives the raw string and can split it on commas. Without
    # it, a single bare ID decodes to an int and a comma list isn't valid JSON.
    admin_allowlist: Annotated[list[int], NoDecode] = Field(
        default_factory=list, alias="ADMIN_ALLOWLIST"
    )
    session_secret: str = Field(default="dev-insecure-secret", alias="SESSION_SECRET")
    public_base_url: str = Field(default="http://localhost:8000", alias="PUBLIC_BASE_URL")

    # ── Local services ───────────────────────────────────────────────────
    database_path: str = Field(default="data/olisar.db", alias="DATABASE_PATH")
    api_host: str = Field(default="127.0.0.1", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    control_host: str = Field(default="127.0.0.1", alias="CONTROL_HOST")
    control_port: int = Field(default=8765, alias="CONTROL_PORT")

    @field_validator("admin_allowlist", "peer_bot_ids", mode="before")
    @classmethod
    def _split_allowlist(cls, v: object) -> list[int]:
        """Accept a comma-separated string (or single id) from the env -> ints."""
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, int):
            return [v]
        return v  # type: ignore[return-value]

    @property
    def oauth_redirect_uri(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/auth/callback"

    @property
    def control_url(self) -> str:
        return f"http://{self.control_host}:{self.control_port}"

    @property
    def async_db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.database_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
