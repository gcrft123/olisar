"""Text-to-image generation: Gemini on a key with billing on, else Cloudflare Workers AI.

Gemini's image models have a free quota of 0, so a free key can't make images with them.
Cloudflare Workers AI runs FLUX.1 [schnell] with a free daily Neuron allocation, so that's
where a free key's images come from, given a Cloudflare account and token. A billed key
makes them on Gemini (Nano Banana 2 Lite, about $0.03 an image), which needs nothing more,
unless the operator turned that off or the month's budget is spent; if Gemini fails and
Cloudflare is set up, Cloudflare makes it instead.

Cloudflare is one REST call:

    POST https://api.cloudflare.com/client/v4/accounts/{id}/ai/run/{model}
    Authorization: Bearer {token}
    {"prompt": "..."}            ->  {"result": {"image": "<base64 jpeg>"}}

Best-effort and never raises: a misconfiguration, HTTP error, daily-allocation
exhaustion, or bad payload all return ``(None, "")`` so the calling tool can tell
the user it couldn't make an image, exactly like the rate-limit path elsewhere.
Cloudflare needs ``CLOUDFLARE_ACCOUNT_ID`` + ``CLOUDFLARE_API_TOKEN`` (the token needs the
Workers AI permission).
"""

from __future__ import annotations

import base64
import logging

import httpx

from google.genai import types

from olisar import runtime_keys
from olisar.config import settings
from olisar.gemini import spend, tier
from olisar.gemini.models import GEMINI_IMAGE_MODEL
from olisar.gemini.rate_limiter import key_id, record_usage

log = logging.getLogger("olisar.imaging")

_RUN_URL = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
_PROMPT_MAX = 2048  # FLUX prompt length cap
_TIMEOUT = 60.0


async def cloudflare_configured() -> bool:
    """True when Cloudflare credentials are present. Reads the effective creds — a
    dashboard entry overrides .env."""
    return bool(await runtime_keys.cloudflare_account_id() and await runtime_keys.cloudflare_api_token())


async def gemini_ready() -> bool:
    """Whether Gemini makes images right now: the key in use has billing on, the operator
    hasn't turned Gemini images off, and the month's budget isn't spent."""
    key = await runtime_keys.gemini_api_key()
    if not key or tier.known(key_id(key)) != tier.PAID:
        return False
    if not (await spend.budget()).gemini_images:
        return False
    return not await spend.over_budget()


async def backend() -> str | None:
    """Where the next image would be made: ``"gemini"``, ``"cloudflare"``, or None."""
    if await gemini_ready():
        return "gemini"
    if await cloudflare_configured():
        return "cloudflare"
    return None


async def is_configured() -> bool:
    """True when image generation is on, one way or the other."""
    return await backend() is not None


async def generate_image(prompt: str) -> tuple[bytes | None, str]:
    """Generate an image from ``prompt``, on Gemini or Cloudflare (see ``backend``).

    Returns ``(image_bytes, mime_type)`` on success, or ``(None, "")`` if image
    generation is unconfigured/unavailable/failed. Never raises.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return None, ""
    if await gemini_ready():
        data, mime = await _generate_gemini(prompt)
        if data or not await cloudflare_configured():
            return data, mime
        log.warning("Gemini couldn't make the image; trying Cloudflare")
    return await _generate_cloudflare(prompt)


async def _generate_gemini(prompt: str) -> tuple[bytes | None, str]:
    from olisar.gemini.client import get_gemini, token_split

    try:
        client = await get_gemini().aclient()
        resp = await client.aio.models.generate_content(
            model=GEMINI_IMAGE_MODEL,
            contents=prompt[:_PROMPT_MAX],
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )
    except Exception:
        log.exception("Gemini image request failed")
        return None, ""
    data, mime = None, ""
    try:
        for part in resp.candidates[0].content.parts or []:
            if part.inline_data and part.inline_data.data:
                data, mime = part.inline_data.data, part.inline_data.mime_type or "image/png"
                break
    except Exception:
        log.exception("Gemini image response wasn't the expected shape")
    tokens, input_tokens, output_tokens = token_split(resp)
    await record_usage(
        GEMINI_IMAGE_MODEL, tokens, source="image",
        input_tokens=input_tokens, output_tokens=output_tokens, images=1 if data else 0,
    )
    if not data:
        log.warning("Gemini returned no image (blocked or empty)")
    return data, mime


async def _generate_cloudflare(prompt: str) -> tuple[bytes | None, str]:
    account = await runtime_keys.cloudflare_account_id()
    token = await runtime_keys.cloudflare_api_token()
    if not (account and token):
        log.warning("generate_image called but Cloudflare credentials are not set")
        return None, ""

    url = _RUN_URL.format(account=account, model=settings.cloudflare_image_model)
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"prompt": prompt[:_PROMPT_MAX]}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, headers=headers, json=payload)
    except Exception:
        log.exception("Cloudflare image request failed to send")
        return None, ""

    if resp.status_code != 200:
        # 401/403 = bad token, 429/5xx = allocation/transient. Surface a short hint.
        log.warning(
            "Cloudflare image gen HTTP %s: %s", resp.status_code, resp.text[:300]
        )
        return None, ""

    try:
        b64 = resp.json()["result"]["image"]
        data = base64.b64decode(b64)
    except Exception:
        log.exception("Cloudflare image response wasn't the expected shape")
        return None, ""

    if not data:
        return None, ""
    return data, "image/jpeg"
