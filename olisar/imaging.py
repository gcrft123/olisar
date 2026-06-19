"""Text-to-image generation via Cloudflare Workers AI (FLUX).

Gemini's image-generation models are paid-only (the free tier's request quota is
literally 0), which clashes with Olisar's free-only constraint. Cloudflare Workers
AI runs FLUX.1 [schnell] with a free daily Neuron allocation, so that's the backend
here. One simple REST call:

    POST https://api.cloudflare.com/client/v4/accounts/{id}/ai/run/{model}
    Authorization: Bearer {token}
    {"prompt": "..."}            ->  {"result": {"image": "<base64 jpeg>"}}

Best-effort and never raises: a misconfiguration, HTTP error, daily-allocation
exhaustion, or bad payload all return ``(None, "")`` so the calling tool can tell
the user it couldn't make an image, exactly like the rate-limit path elsewhere.
Requires ``CLOUDFLARE_ACCOUNT_ID`` + ``CLOUDFLARE_API_TOKEN`` (token needs the
Workers AI permission); without them the feature is simply off.
"""

from __future__ import annotations

import base64
import logging

import httpx

from olisar import runtime_keys
from olisar.config import settings

log = logging.getLogger("olisar.imaging")

_RUN_URL = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
_PROMPT_MAX = 2048  # FLUX prompt length cap
_TIMEOUT = 60.0


async def is_configured() -> bool:
    """True when Cloudflare credentials are present (image generation enabled).
    Reads the effective creds — a dashboard entry overrides .env."""
    return bool(await runtime_keys.cloudflare_account_id() and await runtime_keys.cloudflare_api_token())


async def generate_image(prompt: str) -> tuple[bytes | None, str]:
    """Generate an image from ``prompt`` via Workers AI FLUX.

    Returns ``(jpeg_bytes, "image/jpeg")`` on success, or ``(None, "")`` if image
    generation is unconfigured/unavailable/failed. Never raises.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return None, ""
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
