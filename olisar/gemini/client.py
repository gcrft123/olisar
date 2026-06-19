"""Thin async wrapper around the Gemini SDK.

Adds the things every call should have: rate limiting, fallback across the model
chain on transient/overload errors, usage accounting, and safe text extraction.
Tool/function calling is layered on in Phase 3.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from olisar import runtime_keys
from olisar.config import settings
from olisar.gemini.models import image_model_chain, model_chain
from olisar.gemini.rate_limiter import RateLimitExceeded, get_rate_limiter, record_usage

log = logging.getLogger("olisar.gemini")

# Transient server / overload errors (most often 503 "model overloaded" under high
# demand). Like a 429, these make us fall back to the next model in the chain and
# briefly skip the failing one — rather than hammering an overloaded model.
_TRANSIENT_5XX = {500, 502, 503, 504}
_SERVER_ERROR_COOLDOWN = 15.0  # short skip of a model that just returned a 5xx


class GroundingUnavailable(Exception):
    """Raised when Google Search grounding is quota-exhausted (free tier is small)."""


@dataclass
class GenResult:
    text: str
    tokens: int


def safe_text(resp) -> str:
    """Extract text from a response; `.text` raises if blocked/empty."""
    try:
        return (resp.text or "").strip()
    except Exception:
        return ""


class GeminiClient:
    def __init__(self) -> None:
        self._client: genai.Client | None = None
        self._key: str | None = None

    async def aclient(self) -> genai.Client:
        """The underlying SDK client, built lazily and rebuilt when the effective
        API key changes (a dashboard edit overrides .env without a restart). Raises
        if no key is configured anywhere, so callers degrade rather than crash oddly."""
        key = await runtime_keys.gemini_api_key()
        if not key:
            raise RuntimeError("no Gemini API key configured (set GEMINI_API_KEY or add one in the dashboard)")
        if self._client is None or key != self._key:
            self._client = genai.Client(api_key=key)
            self._key = key
        return self._client

    async def _raw_generate(self, *, contents, config, model: str, chain: list[str] | None = None):
        """Generate, walking the model fallback chain. The first immediately
        available model is used; if it errors transiently — a 429 (rate limit) or a
        5xx (server/overload, e.g. 503 under high demand) — that model is briefly
        parked and we fall back to the next-best model rather than hammering it. A
        non-transient error (e.g. 400/404) is raised. If every model in the chain is
        unavailable, the last error is raised (or RateLimitExceeded if none was hit).

        Pass ``chain`` to override the default chat ranking (e.g. the vision
        chain); otherwise it's derived from ``model`` via ``model_chain``."""
        limiter = get_rate_limiter()
        chain = chain or model_chain(model)
        last_error: Exception | None = None
        for candidate in chain:
            if limiter.state(candidate) != "ok":
                continue  # busy or cooling down — fall back to the next model
            limiter.reserve(candidate)
            try:
                client = await self.aclient()
                resp = await client.aio.models.generate_content(
                    model=candidate, contents=contents, config=config
                )
            except genai_errors.APIError as exc:
                code = getattr(exc, "code", None)
                last_error = exc
                if code == 429:
                    limiter.penalize(candidate, reason="a rate limit (429)")
                elif code in _TRANSIENT_5XX:
                    log.warning(
                        "gemini %s error code=%s; falling back to next model", candidate, code
                    )
                    limiter.penalize(
                        candidate, seconds=_SERVER_ERROR_COOLDOWN, reason=f"a {code} error"
                    )
                else:
                    raise  # non-transient error — surface it, don't mask
                continue  # fall back to the next model in the chain

            tokens = (
                resp.usage_metadata.total_token_count
                if resp.usage_metadata is not None
                else 0
            ) or 0
            await record_usage(candidate, tokens)
            if candidate != chain[0]:
                log.info(
                    "used fallback model %s (preferred %s unavailable)",
                    candidate, chain[0],
                )
            return resp

        # Every model in the chain was unavailable or erroring.
        if last_error is not None:
            raise last_error
        raise RateLimitExceeded(chain[0], "all fallback models")

    async def generate(
        self,
        *,
        contents: list,
        system_instruction: str,
        model: str | None = None,
        temperature: float = 0.9,
        max_output_tokens: int = 600,
    ) -> GenResult:
        model = model or settings.gemini_chat_model
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        resp = await self._raw_generate(contents=contents, config=config, model=model)
        tokens = (
            resp.usage_metadata.total_token_count if resp.usage_metadata is not None else 0
        ) or 0
        return GenResult(text=safe_text(resp), tokens=tokens)

    async def generate_with_tools(
        self,
        *,
        contents: list,
        system_instruction: str,
        tools: list,
        model: str | None = None,
        temperature: float = 0.9,
        max_output_tokens: int = 700,
        force_text: bool = False,
    ):
        """One tool-enabled turn. Returns the raw response so the caller can read
        `.function_calls`. Automatic function calling is disabled — we run the
        loop ourselves so tools get our DB/Discord context. With ``force_text``
        the model is barred from calling tools (function-calling mode NONE), so it
        must answer in plain text — used to close out the loop without a blank."""
        model = model or settings.gemini_chat_model
        tool_config = None
        if force_text:
            tool_config = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="NONE")
            )
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            tools=tools,
            tool_config=tool_config,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        return await self._raw_generate(contents=contents, config=config, model=model)

    async def caption_images(
        self,
        images: list[tuple[bytes, str]],
        *,
        instruction: str,
        model: str | None = None,
        max_output_tokens: int = 220,
    ) -> str:
        """Describe one or more images in plain text, walking the vision fallback
        chain. ``images`` is a list of ``(data, mime_type)``. Returns the model's
        description (or '' if blocked). Raises RateLimitExceeded if every vision
        model is unavailable, so callers can degrade to filename-only."""
        if not images:
            return ""
        parts = [types.Part(text=instruction)]
        for data, mime in images:
            parts.append(types.Part(inline_data=types.Blob(mime_type=mime, data=data)))
        config = types.GenerateContentConfig(
            temperature=0.3, max_output_tokens=max_output_tokens
        )
        chain = image_model_chain(model or settings.gemini_vision_model)
        resp = await self._raw_generate(
            contents=[types.Content(role="user", parts=parts)],
            config=config,
            model=chain[0],
            chain=chain,
        )
        return safe_text(resp)

    async def search(self, query: str, *, model: str | None = None) -> tuple[str, list[str]]:
        """Grounded web search via Google Search. Returns (answer, source titles).
        Raises GroundingUnavailable on quota exhaustion (free-tier grounding is
        tiny) so the caller can degrade rather than crash."""
        model = model or settings.gemini_chat_model
        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )
        await get_rate_limiter().acquire(model)
        try:
            client = await self.aclient()
            resp = await client.aio.models.generate_content(
                model=model, contents=query, config=config
            )
        except genai_errors.APIError as exc:
            if getattr(exc, "code", None) == 429:
                raise GroundingUnavailable() from exc
            raise
        tokens = (
            resp.usage_metadata.total_token_count if resp.usage_metadata is not None else 0
        ) or 0
        await record_usage(model, tokens, grounding=1)

        sources: list[str] = []
        try:
            chunks = resp.candidates[0].grounding_metadata.grounding_chunks or []
            for ch in chunks:
                if ch.web and ch.web.uri:
                    sources.append(ch.web.title or ch.web.uri)
        except Exception:
            pass
        return safe_text(resp), sources


_gemini: GeminiClient | None = None


def get_gemini() -> GeminiClient:
    global _gemini
    if _gemini is None:
        _gemini = GeminiClient()
    return _gemini
