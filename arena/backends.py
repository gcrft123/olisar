"""Where the harness's own model calls go — Gemini, or Claude through its CLI.

Two roles, and they want different things:

**dialogue** — the emulators' chat lines. High volume, throwaway, and the one thing that
matters is voice. It also must not compete for quota with the bot under test, which is the
whole reason this is pluggable: Gemini's free tier is ~10 RPM shared between Olisar, the
emulators and the judge, so a busy scenario starves the thing being measured. Claude Haiku
through the CLI moves that load onto a different account entirely.

**judge** — verdicts everything downstream trusts. Low volume, and worth spending on.

The Claude backend shells out to ``claude -p``. Four flags do the work:

``--safe-mode``       no CLAUDE.md, skills, plugins, hooks, or MCP. Without it the harness
                      inherits this repo's own agent configuration, and the emulators start
                      writing like a coding assistant that has read the codebase.
``--tools ""``        no tool definitions in the request. This is worth 22k cached input
                      tokens per call — measured at ~$0.047 a line with tools, ~$0.005
                      without, for identical output.
``MAX_THINKING_TOKENS=0``  (dialogue only) a two-word chat message does not need 800 thinking
                      tokens. Setting it takes a line from ~$0.005/10s to ~$0.0009/1.8s.
                      Deliberately *not* set for the judge, which does benefit from thinking.
``--json-schema``     the API enforces the shape, so structured output stops depending on the
                      model's willingness to skip a code fence.

Note on billing: ``claude -p`` uses whatever credentials the CLI is logged in with. On a
subscription that means arena runs draw down the same usage limits as interactive work. Set
``ANTHROPIC_API_KEY`` in the environment to bill API credits instead.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger("arena.backends")

CLAUDE = "claude"
GEMINI = "gemini"
GROK = "grok"
BACKENDS = (CLAUDE, GEMINI, GROK)

# Gemini's Flash-Lite is the cheap tier for throwaway text; Haiku is Claude's.
DEFAULT_GEMINI_DIALOGUE_MODEL = "gemini-3.1-flash-lite"
DEFAULT_CLAUDE_DIALOGUE_MODEL = "haiku"


@dataclass
class Completion:
    """One backend call's result, plus what it cost us to know."""

    text: str = ""
    usd: float = 0.0
    error: str = ""


class Backend(Protocol):
    """Text in, text out. Implementations must never raise on a model-side failure —
    a run that dies because one emulator line failed to generate loses the whole
    transcript, and the runner already treats an empty line as a skipped turn."""

    name: str
    model: str

    async def complete(
        self, prompt: str, *, system: str, temperature: float, max_output_tokens: int,
        schema: dict | None = None,
    ) -> Completion: ...


def extract_json(raw: str) -> dict:
    """Parse a JSON object out of model text, tolerating a code fence or prose around it."""
    if not raw:
        return {}
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ── Claude, via the CLI ───────────────────────────────────────────────────


class ClaudeCliBackend:
    """``claude -p`` as a one-shot completion API."""

    name = CLAUDE

    def __init__(
        self,
        model: str,
        *,
        binary: str = "claude",
        thinking: bool = False,
        timeout: float = 120.0,
        cwd: str | None = None,
    ) -> None:
        self.model = model
        self._binary = binary
        self._thinking = thinking
        self._timeout = timeout
        self._cwd = cwd

    @classmethod
    def available(cls, binary: str = "claude") -> bool:
        return shutil.which(binary) is not None

    def _argv(self, prompt: str, system: str, schema: dict | None) -> list[str]:
        argv = [
            self._binary,
            "-p",
            # No CLAUDE.md, skills, plugins, hooks, MCP, or custom agents. The harness must
            # behave the same on any machine, including one whose repo config would
            # otherwise be folded into every emulator's voice.
            "--safe-mode",
            "--no-session-persistence",
            "--disable-slash-commands",
            "--strict-mcp-config",
            # An emulator writes a chat message; it has no business reading files. This also
            # removes the tool schemas from the request, which dominate the cost otherwise.
            "--tools",
            "",
            "--model",
            self.model,
            "--output-format",
            "json",
        ]
        if system:
            argv += ["--system-prompt", system]
        if schema:
            argv += ["--json-schema", json.dumps(schema)]
        argv.append(prompt)
        return argv

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if not self._thinking:
            env["MAX_THINKING_TOKENS"] = "0"
        return env

    async def complete(
        self, prompt: str, *, system: str = "", temperature: float = 1.0,
        max_output_tokens: int = 400, schema: dict | None = None,
    ) -> Completion:
        # temperature and max_output_tokens have no CLI equivalent. Length is governed by
        # the prompt and trimmed downstream (arena.fleet.dialogue._tidy); saying so here
        # beats silently accepting arguments that do nothing.
        del temperature, max_output_tokens

        try:
            process = await asyncio.create_subprocess_exec(
                *self._argv(prompt, system, schema),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                env=self._env(),
                cwd=self._cwd,
            )
        except (OSError, FileNotFoundError) as exc:
            return Completion(error=f"couldn't run {self._binary!r}: {exc}")

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return Completion(error=f"{self._binary} timed out after {self._timeout:.0f}s")

        if process.returncode != 0:
            detail = (stderr.decode("utf-8", "replace") or stdout.decode("utf-8", "replace"))[:400]
            return Completion(error=f"{self._binary} exited {process.returncode}: {detail}")

        try:
            payload = json.loads(stdout.decode("utf-8", "replace"))
        except ValueError:
            return Completion(error=f"unparseable CLI output: {stdout[:200]!r}")

        usd = float(payload.get("total_cost_usd", 0.0) or 0.0)
        if payload.get("is_error"):
            return Completion(
                usd=usd,
                error=str(payload.get("result") or payload.get("api_error_status") or "CLI error"),
            )
        return Completion(text=str(payload.get("result", "")).strip(), usd=usd)


# ── Grok, via its CLI ─────────────────────────────────────────────────────


class GrokCliBackend:
    """``grok -p`` as a one-shot completion API.

    Same shape as the Claude backend and a different dialect: the prompt is a flag value
    rather than a positional, the system prompt is ``--system-prompt-override``, reasoning
    is a named effort rather than a token budget, and the reply comes back under ``text``.

    Exists as a second judge option so a spent Claude budget doesn't stop the work. Note
    it is *not* interchangeable mid-experiment: switching the judge changes what every
    score means, so a comparison must run entirely on one or entirely on the other, and
    ``arena calibrate`` should be re-run after any switch.
    """

    name = GROK

    def __init__(
        self,
        model: str,
        *,
        binary: str = "grok",
        effort: str = "high",
        timeout: float = 180.0,
        cwd: str | None = None,
    ) -> None:
        self.model = model
        self._binary = binary
        self._effort = effort
        self._timeout = timeout
        self._cwd = cwd

    @classmethod
    def available(cls, binary: str = "grok") -> bool:
        return shutil.which(binary) is not None

    def _argv(self, prompt: str, system: str, schema: dict | None) -> list[str]:
        argv = [
            self._binary,
            "-p",
            prompt,
            "--model",
            self.model,
            "--reasoning-effort",
            self._effort,
            # No tools and no web search: the judge grades a transcript it was handed,
            # and anything it could look up is a way for one run to differ from another.
            "--tools",
            "",
            "--disable-web-search",
            "--output-format",
            "json",
        ]
        if system:
            argv += ["--system-prompt-override", system]
        if schema:
            argv += ["--json-schema", json.dumps(schema)]
        return argv

    async def complete(
        self, prompt: str, *, system: str = "", temperature: float = 1.0,
        max_output_tokens: int = 400, schema: dict | None = None,
    ) -> Completion:
        del temperature, max_output_tokens  # no CLI equivalent; see ClaudeCliBackend
        try:
            process = await asyncio.create_subprocess_exec(
                *self._argv(prompt, system, schema),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=self._cwd,
            )
        except (OSError, FileNotFoundError) as exc:
            return Completion(error=f"couldn't run {self._binary!r}: {exc}")
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return Completion(error=f"{self._binary} timed out after {self._timeout:.0f}s")
        if process.returncode != 0:
            detail = (stderr.decode("utf-8", "replace") or stdout.decode("utf-8", "replace"))[:400]
            return Completion(error=f"{self._binary} exited {process.returncode}: {detail}")
        try:
            payload = json.loads(stdout.decode("utf-8", "replace"))
        except ValueError:
            return Completion(error=f"unparseable CLI output: {stdout[:200]!r}")
        usd = float(payload.get("total_cost_usd", 0.0) or 0.0)
        text = str(payload.get("text", "")).strip()
        if not text:
            return Completion(usd=usd, error=f"empty reply (stop: {payload.get('stopReason')})")
        return Completion(text=text, usd=usd)


# ── Gemini, via the SDK ───────────────────────────────────────────────────


class GeminiBackend:
    """The google-genai SDK. Shares a free-tier key with the bot under test."""

    name = GEMINI

    def __init__(self, model: str, api_key: str) -> None:
        if not api_key:
            raise ValueError("the Gemini backend needs GEMINI_API_KEY")
        from google import genai

        self.model = model
        self._client = genai.Client(api_key=api_key)

    async def complete(
        self, prompt: str, *, system: str = "", temperature: float = 1.0,
        max_output_tokens: int = 400, schema: dict | None = None,
    ) -> Completion:
        from google.genai import types

        # The SDK has no CLI-style schema enforcement here, so a JSON request falls back to
        # asking for one and parsing what comes back (see ModelClient.generate_json).
        del schema
        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            system_instruction=system or None,
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self.model, contents=prompt, config=config
            )
        except Exception as exc:  # noqa: BLE001 — every provider error is the same to us
            return Completion(error=f"{type(exc).__name__}: {exc}")
        try:
            return Completion(text=(response.text or "").strip())
        except Exception:
            return Completion(error="response carried no text (blocked or empty)")


def build(
    kind: str,
    model: str,
    *,
    gemini_api_key: str = "",
    claude_binary: str = "claude",
    grok_binary: str = "grok",
    grok_effort: str = "high",
    thinking: bool = False,
    cwd: str | None = None,
) -> Backend:
    """Construct a backend, failing with a message that says what to change."""
    kind = (kind or "").strip().lower()
    if kind == CLAUDE:
        if not ClaudeCliBackend.available(claude_binary):
            raise ValueError(
                f"{claude_binary!r} is not on PATH. Install the Claude CLI, or switch the "
                f"backend to '{GEMINI}'."
            )
        return ClaudeCliBackend(model, binary=claude_binary, thinking=thinking, cwd=cwd)
    if kind == GROK:
        if not GrokCliBackend.available(grok_binary):
            raise ValueError(
                f"{grok_binary!r} is not on PATH. Install the Grok CLI, or use "
                f"'{CLAUDE}' / '{GEMINI}'."
            )
        return GrokCliBackend(model, binary=grok_binary, effort=grok_effort, cwd=cwd)
    if kind == GEMINI:
        return GeminiBackend(model, gemini_api_key)
    raise ValueError(f"unknown backend {kind!r}; expected one of {', '.join(BACKENDS)}")
