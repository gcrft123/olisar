"""Publish-time AI security review of an extension's source.

Runs the operator's own Gemini model over the extension source + manifest and returns a
0-100 risk score plus a short bullet rationale. Used two ways:
  * at PUBLISH, to block shipping an extension whose score is at/above the operator's
    threshold (``runtime_config.extension_risk_threshold``) and to stamp the score onto
    the registry listing; and
  * at INSTALL, to show the installer a fresh, trustworthy assessment in the consent
    screen (re-run on their side, so a malicious publisher can't fake a low score).

The review never raises on a model hiccup — it returns ``ok=False`` ("review unavailable")
so a transient Gemini error can't brick publishing; the caller decides how to treat that.
"""

from __future__ import annotations

import json
import logging
import re

log = logging.getLogger("olisar.extensions.review")

_SYSTEM = (
    "You are a security reviewer for a sandboxed Discord-bot extension marketplace. "
    "Extensions run in a locked-down JS sandbox: no filesystem, no arbitrary network "
    "except host.fetch (public HTTP(S) only, no private/loopback hosts), and they can use "
    "only the capabilities the operator grants (kv, fetch, discord.reply/components/send, "
    "model.generate, host.secret is unavailable to third parties). Assess how risky it "
    "would be for a server operator to install this third-party extension. Weigh: data "
    "exfiltration (sending server/user content to an external URL via host.fetch), "
    "unsolicited or spammy channel posts (host.discord.send), prompt-injection or "
    "social-engineering of the model, behavior that contradicts the stated description, "
    "asking for more permissions than the behavior needs, and obfuscated or obscured "
    "logic. Benign, transparent utility code is LOW risk; clearly abusive or deceptive "
    "code is HIGH. Respond with ONLY a JSON object — no prose, no code fence:\n"
    '{"score": <int 0-100>, "summary": "<one sentence>", "bullets": ["<short point>", ...]}\n'
    "Scoring: 0-30 low risk, 31-69 some concerns, 70-100 high risk. Give 2 to 5 bullets."
)


def _build_prompt(source: str, manifest: dict, requested_permissions: list[str]) -> str:
    name = manifest.get("name") or manifest.get("id") or "(unnamed)"
    desc = manifest.get("description") or "(none)"
    tools = ", ".join(t.get("name", "") for t in manifest.get("tools", []) if t.get("name")) or "(none)"
    cmds = ", ".join(c.get("name", "") for c in manifest.get("commands", []) if c.get("name")) or "(none)"
    perms = ", ".join(requested_permissions) or "(none)"
    src = source if len(source) <= 24000 else source[:24000] + "\n/* …truncated for review… */"
    return (
        f"Extension: {name}\nStated description: {desc}\n"
        f"Requested permissions: {perms}\nTools: {tools}\nSlash commands: {cmds}\n\n"
        f"Source:\n```\n{src}\n```"
    )


def _parse(text: str) -> dict | None:
    """Pull the JSON object out of the model's reply, tolerating a stray code fence."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text.strip(), re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    try:
        score = int(round(float(obj.get("score"))))
    except (TypeError, ValueError):
        return None
    score = max(0, min(score, 100))
    bullets = [str(b).strip() for b in (obj.get("bullets") or []) if str(b).strip()][:6]
    summary = str(obj.get("summary") or "").strip()[:300]
    return {"score": score, "summary": summary, "bullets": bullets}


async def review_source(
    source: str, manifest: dict, *, requested_permissions: list[str] | None = None
) -> dict:
    """Review an extension's source. Returns ``{score, summary, bullets, ok}``. ``ok=False``
    means the model couldn't be reached or parsed — treat that as "review unavailable", not
    as a guarantee of safety."""
    perms = list(requested_permissions or manifest.get("permissions") or [])
    unavailable = {"score": 0, "summary": "Automated review unavailable.", "bullets": [], "ok": False}
    if not (source or "").strip():
        return unavailable
    try:
        from google.genai import types

        from olisar.gemini.client import get_gemini

        result = await get_gemini().generate(
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part(text=_build_prompt(source, manifest, perms))],
                )
            ],
            system_instruction=_SYSTEM,
            max_output_tokens=700,
            temperature=0.2,
        )
    except Exception:  # noqa: BLE001 - never brick publish/install on a model hiccup
        log.warning("extension review call failed", exc_info=True)
        return unavailable
    parsed = _parse(result.text or "")
    if parsed is None:
        log.warning("extension review returned unparseable output")
        return unavailable
    parsed["ok"] = True
    return parsed
