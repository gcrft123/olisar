"""Arena configuration: what the harness needs, and how the instance-under-test is launched.

Everything lives in ``.env.arena`` at the repo root (gitignored — it holds bot tokens).
That file carries only the *deltas* from the developer's ordinary ``.env``: the arena's own
Discord app, its guild, its data directory and ports. The Olisar process is started with
``.env``'s environment plus these overrides, so the Gemini key and everything else are
inherited rather than duplicated into a second file that will drift.

The arena instance is isolated from a developer's normal instance along every axis that
can collide: a different Discord application (so it is a different bot user), a different
guild, a different ``OLISAR_DATA_DIR`` (so a different SQLite database and upload dir), and
different API/control ports. Nothing here can reach the production VM.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env.arena"
BASE_ENV_FILE = REPO_ROOT / ".env"
RUNS_DIR = REPO_ROOT / "arena" / "runs"
PERSONAS_DIR = REPO_ROOT / "arena" / "personas"
SCENARIOS_DIR = REPO_ROOT / "arena" / "scenarios"
VARIANTS_DIR = REPO_ROOT / "arena" / "variants"

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


class ConfigError(RuntimeError):
    """Raised when the arena is asked to do something its config can't support.

    Carries an actionable message: every one of these is a missing value in
    ``.env.arena`` that the operator has to supply, not a bug to debug.
    """


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a ``.env``-style file into a dict. Deliberately minimal — comments, blank
    lines, ``export`` prefixes, and surrounding quotes; no interpolation. Missing file
    yields ``{}`` so the arena can report what's absent rather than crash on import."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = _LINE.match(raw)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


@dataclass(frozen=True)
class ArenaConfig:
    """Resolved arena settings. Construct with :func:`load`."""

    # ── the Olisar instance under test ────────────────────────────────────
    discord_token: str
    guild_id: int
    operator_id: int
    data_dir: Path
    api_port: int
    control_port: int

    # ── server management ─────────────────────────────────────────────────
    steward_token: str

    # ── the emulator fleet: persona key -> bot token ──────────────────────
    fleet_tokens: dict[str, str] = field(default_factory=dict)

    # ── where the harness's own model calls go ────────────────────────────
    # Split by role, and both on the Claude CLI by default so that the free-tier Gemini
    # quota belongs entirely to the instance under test. Sharing it was a measurement
    # problem, not just a throughput one: a harness competing for the same ~10 RPM starves
    # Olisar of exactly the quota whose absence then gets scored as a bad reply.
    #
    # Haiku for dialogue (high volume, throwaway, ~$0.001/line), Sonnet for judging (low
    # volume, and every verdict downstream rests on it). Switching either is one env var;
    # re-run `arena calibrate` after changing the judge.
    dialogue_backend: str = "claude"
    dialogue_model: str = "haiku"
    judge_backend: str = "claude"
    judge_model: str = "sonnet"
    claude_binary: str = "claude"
    grok_binary: str = "grok"
    grok_effort: str = "high"
    gemini_api_key: str = ""

    # ── governors ─────────────────────────────────────────────────────────
    # A scenario that never ends is the default failure mode of a bot fleet: Olisar
    # answers, an emulator reacts, Olisar answers again. These are the hard stops.
    max_messages_per_scenario: int = 60
    min_seconds_between_fleet_messages: float = 2.5
    scenario_timeout_seconds: float = 420.0
    # Free-tier Gemini is ~10 RPM on the top chat model with a 7-model fallback chain
    # (olisar/gemini/models.py), shared with the bot under test — so the Gemini ceiling is
    # a call count. Claude is billed (or drawn from a subscription's limits), so its
    # ceiling is dollars. Both are hard stops; see arena/model.py.
    daily_model_call_budget: int = 800
    claude_daily_usd: float = 5.0
    # Grok has its own ceiling. Deliberately separate: it is the fallback for when the
    # Claude budget is spent, so sharing one pool would defeat the point.
    grok_daily_usd: float = 5.0

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self.api_port}"

    @property
    def repo_root(self) -> Path:
        """Where subprocesses (the instance, the Claude CLI) are launched from."""
        return REPO_ROOT

    @property
    def prompt_overrides_path(self) -> Path:
        """Where the active baked-in prompt variant is written. Handed to the Olisar
        process as ``OLISAR_PROMPT_OVERRIDES`` (see olisar/prompt_overrides.py)."""
        return self.data_dir / "prompt_overrides.json"

    def require(self, *fields: str) -> None:
        """Assert that the named settings are non-empty, with a message naming the
        ``.env.arena`` key to fill in. Commands call this for what they actually need, so
        a fleet-free command still works before any emulator app exists."""
        missing = [f for f in fields if not getattr(self, f, None)]
        if missing:
            keys = ", ".join(_ENV_KEY_FOR.get(f, f.upper()) for f in missing)
            raise ConfigError(
                f"missing arena config: {keys}\n"
                f"Fill it in at {ENV_FILE} — see arena/README.md for how to get each value."
            )

    def child_env(self) -> dict[str, str]:
        """The environment the Olisar-under-test process is launched with: the developer's
        own ``.env`` (for the Gemini key and anything else), then the arena's overrides.

        ``OLISAR_PEER_BOT_IDS`` is filled from the fleet's *resolved* ids, which are only
        known once the emulators have connected at least once — see
        ``arena.fleet.registry``. Until then it is empty and Olisar simply ignores the
        emulators, which shows up immediately as "the fleet talks and nothing happens".
        """
        from arena.fleet.registry import peer_ids  # local: avoids an import cycle

        env = dict(os.environ)
        env.update(read_env_file(BASE_ENV_FILE))
        env.update(read_env_file(ENV_FILE))
        ids = peer_ids(self)
        env.update(
            {
                "DISCORD_TOKEN": self.discord_token,
                "TARGET_GUILD_ID": str(self.guild_id),
                "OLISAR_DATA_DIR": str(self.data_dir),
                "DATABASE_PATH": str(self.data_dir / "olisar.db"),
                "API_PORT": str(self.api_port),
                "OLISAR_PORT": str(self.api_port),
                "CONTROL_PORT": str(self.control_port),
                "ADMIN_ALLOWLIST": str(self.operator_id),
                "OLISAR_PEER_BOT_IDS": ",".join(str(i) for i in ids),
                "OLISAR_PROMPT_OVERRIDES": str(self.prompt_overrides_path),
                # The arena is loopback-only and never publishes a tunnel.
                "PUBLIC_BASE_URL": f"http://127.0.0.1:{self.api_port}",
                "OLISAR_HEADLESS": "0",
            }
        )
        return env


# Maps a dataclass field to the ``.env.arena`` key that supplies it, so `require`
# can name the thing the operator actually has to edit.
_ENV_KEY_FOR = {
    "discord_token": "ARENA_DISCORD_TOKEN",
    "guild_id": "ARENA_GUILD_ID",
    "operator_id": "ARENA_OPERATOR_ID",
    "steward_token": "ARENA_STEWARD_TOKEN",
    "gemini_api_key": "GEMINI_API_KEY (in .env)",
    "fleet_tokens": "ARENA_BOT_TOKEN_<PERSONA> (at least one)",
}

_FLEET_PREFIX = "ARENA_BOT_TOKEN_"


def _int(values: dict[str, str], key: str, default: int = 0) -> int:
    try:
        return int((values.get(key) or "").strip() or default)
    except ValueError:
        return default


def _float(values: dict[str, str], key: str, default: float) -> float:
    try:
        return float((values.get(key) or "").strip() or default)
    except ValueError:
        return default


def load() -> ArenaConfig:
    """Read ``.env.arena`` (falling back to the process environment) into a config.

    Never raises on a missing file or missing keys — commands assert what they need via
    :meth:`ArenaConfig.require`, so ``arena doctor`` can run and report on an empty setup.
    """
    values = dict(os.environ)
    values.update(read_env_file(BASE_ENV_FILE))
    values.update(read_env_file(ENV_FILE))

    data_dir = Path(values.get("ARENA_DATA_DIR") or (REPO_ROOT / "data" / "arena"))
    if not data_dir.is_absolute():
        data_dir = REPO_ROOT / data_dir

    fleet = {
        key[len(_FLEET_PREFIX):].lower(): value.strip()
        for key, value in values.items()
        if key.startswith(_FLEET_PREFIX) and value.strip()
    }

    return ArenaConfig(
        discord_token=(values.get("ARENA_DISCORD_TOKEN") or "").strip(),
        guild_id=_int(values, "ARENA_GUILD_ID"),
        operator_id=_int(values, "ARENA_OPERATOR_ID"),
        data_dir=data_dir,
        api_port=_int(values, "ARENA_API_PORT", 8770),
        control_port=_int(values, "ARENA_CONTROL_PORT", 8771),
        steward_token=(values.get("ARENA_STEWARD_TOKEN") or "").strip(),
        fleet_tokens=fleet,
        dialogue_backend=(values.get("ARENA_DIALOGUE_BACKEND") or "claude").strip().lower(),
        dialogue_model=(values.get("ARENA_DIALOGUE_MODEL") or "haiku").strip(),
        judge_backend=(values.get("ARENA_JUDGE_BACKEND") or "claude").strip().lower(),
        judge_model=(values.get("ARENA_JUDGE_MODEL") or "sonnet").strip(),
        claude_binary=(values.get("ARENA_CLAUDE_BIN") or "claude").strip(),
        grok_binary=(values.get("ARENA_GROK_BIN") or "grok").strip(),
        grok_effort=(values.get("ARENA_GROK_EFFORT") or "high").strip(),
        gemini_api_key=(values.get("GEMINI_API_KEY") or "").strip(),
        max_messages_per_scenario=_int(values, "ARENA_MAX_MESSAGES", 60),
        min_seconds_between_fleet_messages=_float(values, "ARENA_MIN_GAP_SECONDS", 2.5),
        scenario_timeout_seconds=_float(values, "ARENA_SCENARIO_TIMEOUT", 420.0),
        daily_model_call_budget=_int(values, "ARENA_DAILY_CALL_BUDGET", 800),
        claude_daily_usd=_float(values, "ARENA_CLAUDE_DAILY_USD", 5.0),
        grok_daily_usd=_float(values, "ARENA_GROK_DAILY_USD", 5.0),
    )
