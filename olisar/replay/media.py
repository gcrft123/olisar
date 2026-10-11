"""The pictures in the messages a replay quotes: GIFs, images and stickers.

The search index keeps a message as text, so a GIF is a Tenor or Klipy link, an upload is
``[image: name.png]`` and a sticker is ``[sticker: Wave]``. Those mean nothing on screen. As
the last step of a run the bot looks each quoted message up in Discord and keeps what it
holds, and the text loses the markers and links the pictures stand for:

* A GIF (a ``gifv`` embed) keeps its own video and still, which Tenor and Klipy serve from
  addresses that don't expire.
* A sticker keeps Discord's address for it, which doesn't expire either. Lottie stickers
  (animations Discord draws itself) can't be shown, and stay a name.
* An upload's address expires within a day, so only its place in the message is kept, and
  the console asks for it through ``/api/replay/media``, which looks the message up again
  and sends the browser to a fresh address (api/routers/replay.py).
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger("olisar.replay")

_MARKER = re.compile(r"\[(image|file|sticker|embed): ([^\]\n]*)\]")
_GIF_LINK = re.compile(r"https?://(?:[\w-]+\.)*(?:tenor|klipy|giphy)\.com/[^\s\]\)>]+", re.I)
_PICTURE = re.compile(r"\.(?:gif|png|jpe?g|webp)(?:\?\S*)?$", re.I)
LIMIT = 4   # pictures kept per message


def hinted(text: str) -> bool:
    """Whether a message's indexed text suggests a picture worth looking up."""
    return bool(_MARKER.search(text or "") or _GIF_LINK.search(text or ""))


def clean(text: str, media: list[dict]) -> str:
    """The text without the markers and links its pictures stand for."""
    kinds = {m["kind"] for m in media}
    out = text or ""
    if kinds & {"image", "video"}:
        out = re.sub(r"\[(?:image|file): [^\]\n]*\]", "", out)
    if "sticker" in kinds:
        out = re.sub(r"\[sticker: [^\]\n]*\]", "", out)
    if "gif" in kinds:
        out = re.sub(r"\[embed: [^\]\n]*\]", "", out)
        out = _GIF_LINK.sub("", out)
    return re.sub(r"[ \t]+\n|\n{2,}", "\n", out).strip()


def from_message(msg) -> list[dict]:
    """What a discord.py Message holds that the replay can show."""
    out: list[dict] = []
    for i, att in enumerate(getattr(msg, "attachments", []) or []):
        kind = (att.content_type or "").split("/")[0]
        if kind == "image" or (not att.content_type and _PICTURE.search(att.filename or "")):
            out.append({"kind": "image", "i": i, "w": att.width, "h": att.height})
        elif kind == "video":
            out.append({"kind": "video", "i": i, "w": att.width, "h": att.height})
    for e in getattr(msg, "embeds", []) or []:
        if e.type not in ("gifv", "image"):
            continue
        video = getattr(e.video, "url", None) if e.video else None
        still = getattr(e.thumbnail, "url", None) if e.thumbnail else None
        size = e.video if video else e.thumbnail
        if video or still:
            out.append({"kind": "gif", "src": video or still, "poster": still,
                        "w": getattr(size, "width", None), "h": getattr(size, "height", None)})
    for s in getattr(msg, "stickers", []) or []:
        fmt = str(getattr(getattr(s, "format", None), "name", "")).lower()
        if fmt and fmt != "lottie" and getattr(s, "url", None):
            out.append({"kind": "sticker", "src": str(s.url), "name": s.name, "w": 160, "h": 160})
    return out[:LIMIT]


def _messages(report: dict) -> list[dict]:
    """Every message the replay quotes, wherever it sits in the report."""
    out: list[dict] = []
    out += (report.get("first") or {}).get("messages", [])
    out += [f["msg"] for f in report.get("founders") or [] if f.get("msg")]
    night = report.get("night") or {}
    out += [m for m in night.get("excerpt") or [] if "text" in m]
    out += (report.get("arrive") or {}).get("reply", [])
    pick = report.get("pick") or {}
    if pick.get("origin"):
        out.append(pick["origin"])
    return out


async def attach(report: dict, roster) -> int:
    """Look up the quoted messages that look like they hold a picture, and keep what they
    hold. Returns how many gained one. A message Discord no longer has keeps its text."""
    lookup = getattr(roster, "media", None)
    if lookup is None:
        return 0
    seen: dict[str, list[dict] | None] = {}
    gained = 0
    for m in _messages(report):
        if not hinted(m.get("text", "")):
            continue
        if m["id"] not in seen:
            try:
                seen[m["id"]] = await lookup(int(m["ch"]), int(m["id"]))
            except Exception:  # noqa: BLE001 — a picture is never worth failing the run
                log.info("replay: couldn't look up message %s for its pictures", m["id"])
                seen[m["id"]] = None
        media = seen[m["id"]]
        if media:
            m["media"] = media
            m["text"] = clean(m["text"], media)
            gained += 1
    return gained
