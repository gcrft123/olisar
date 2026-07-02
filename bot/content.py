"""Turn a Discord message into the text Olisar actually stores and searches.

A message is more than ``message.content``: announcement bots post pure embeds,
people drop screenshots with no caption, stickers carry meaning. Everything that
only read ``.content`` silently dropped all of that. ``message_text`` folds the
visible payload — content, embeds, attachment filenames, stickers — into one
flat string so the search index, conversational memory, and context snapshots all
see it. Image *bytes* (for vision) are handled separately by ``download_images``.

Kept in ``bot/`` because it touches ``discord`` types; the ``olisar`` core stays
Discord-agnostic.
"""

from __future__ import annotations

import logging

import discord

log = logging.getLogger("olisar.content")

MAX_IMAGES = 3            # images per message handed to vision (bounds tokens/cost)
MAX_IMAGE_BYTES = 5_000_000  # skip anything larger (free-tier request size + latency)

# Distinct markers so the one-time vision caption ([image description: ...]) is
# told apart from the filename marker ([image: foo.png]) — the captioner keys off
# this to avoid double-describing. Keep these literals in sync with
# olisar/memory/media.py.
IMAGE_DESCRIPTION_PREFIX = "[image description:"


def _embed_text(embed: discord.Embed) -> str:
    """Flatten one embed's human-readable fields into a single line."""
    parts: list[str] = []
    if embed.title:
        parts.append(embed.title)
    if embed.author and embed.author.name:
        parts.append(embed.author.name)
    if embed.description:
        parts.append(embed.description)
    for field in embed.fields:
        name = (field.name or "").strip()
        value = (field.value or "").strip()
        joined = f"{name}: {value}".strip(": ").strip()
        if joined:
            parts.append(joined)
    if embed.footer and embed.footer.text:
        parts.append(embed.footer.text)
    if embed.url:
        parts.append(embed.url)  # keep links searchable
    return " — ".join(p for p in parts if p)


def message_text(message: discord.Message) -> str:
    """The full searchable/contextual text of a message: content + embeds +
    attachment filenames + stickers. Never raises; returns '' only for a truly
    empty message (no text, embed, attachment, or sticker)."""
    chunks: list[str] = []
    if message.content:
        chunks.append(message.content)
    for embed in message.embeds:
        text = _embed_text(embed)
        if text:
            chunks.append(f"[embed: {text}]")
    for att in message.attachments:
        is_image = (att.content_type or "").startswith("image/")
        chunks.append(f"[image: {att.filename}]" if is_image else f"[file: {att.filename}]")
    for sticker in message.stickers:
        chunks.append(f"[sticker: {sticker.name}]")
    return "\n".join(chunks).strip()


def _raw_embed_text(embed: dict) -> str:
    """``_embed_text`` for a raw gateway embed dict (used by raw edit events,
    where we only have the JSON payload, not a parsed ``discord.Embed``)."""
    parts: list[str] = []
    for key in ("title", "description", "url"):
        if embed.get(key):
            parts.append(embed[key])
    author = (embed.get("author") or {}).get("name")
    if author:
        parts.append(author)
    for field in embed.get("fields", []) or []:
        name = (field.get("name") or "").strip()
        value = (field.get("value") or "").strip()
        joined = f"{name}: {value}".strip(": ").strip()
        if joined:
            parts.append(joined)
    footer = (embed.get("footer") or {}).get("text")
    if footer:
        parts.append(footer)
    return " — ".join(p for p in parts if p)


def raw_message_text(data: dict) -> str:
    """``message_text`` for the raw payload in a ``RawMessageUpdateEvent`` — the
    edited message often isn't in the cache, so we rebuild from the gateway dict."""
    chunks: list[str] = []
    if data.get("content"):
        chunks.append(data["content"])
    for embed in data.get("embeds", []) or []:
        text = _raw_embed_text(embed)
        if text:
            chunks.append(f"[embed: {text}]")
    for att in data.get("attachments", []) or []:
        filename = att.get("filename", "file")
        is_image = (att.get("content_type") or "").startswith("image/")
        chunks.append(f"[image: {filename}]" if is_image else f"[file: {filename}]")
    return "\n".join(chunks).strip()


async def resolve_reply(message: discord.Message) -> tuple[str, str] | None:
    """The ``(author display name, text)`` of the message this one replies to, or
    ``None`` when it isn't a reply / the target was deleted / can't be fetched.

    This is what lets Olisar be *aware* of reply context. It deliberately stops at
    surfacing the quoted message — whether that context is actually relevant is the
    model's call (see ``CONTEXT_NOTE``), so an off-topic reply doesn't drag the old
    message into the answer. Best-effort: network/permission failures return None."""
    ref = getattr(message, "reference", None)
    if ref is None or ref.message_id is None:
        return None
    target = ref.resolved if isinstance(ref.resolved, discord.Message) else None
    if target is None:
        if isinstance(ref.resolved, discord.DeletedReferencedMessage):
            return None
        try:  # not resolved on the gateway payload — fetch it once
            target = await message.channel.fetch_message(ref.message_id)
        except Exception:
            log.debug("couldn't fetch replied-to message %s", ref.message_id)
            return None
    text = message_text(target).strip()
    if not text:
        return None
    return (target.author.display_name, text)


def image_attachments(message: discord.Message) -> list[discord.Attachment]:
    """The message's image attachments worth sending to vision (capped + size-bounded)."""
    out: list[discord.Attachment] = []
    for att in message.attachments:
        if not (att.content_type or "").startswith("image/"):
            continue
        if (att.size or 0) > MAX_IMAGE_BYTES:
            continue
        out.append(att)
        if len(out) >= MAX_IMAGES:
            break
    return out


def _gif_first_frame(data: bytes) -> tuple[bytes, str] | None:
    """Flatten a GIF to a PNG of its first frame, plus a note describing what the model
    is actually seeing. Gemini vision doesn't accept ``image/gif`` (and couldn't see the
    motion anyway), so a GIF is otherwise dropped; this lets the bot read it as a still
    while staying honest that it's one frame of a GIF. ``None`` if it can't be decoded."""
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            animated = bool(getattr(im, "is_animated", False)) and getattr(im, "n_frames", 1) > 1
            im.seek(0)
            frame = im.convert("RGBA")
            buf = io.BytesIO()
            frame.save(buf, format="PNG")
    except Exception:
        log.debug("couldn't extract GIF first frame")
        return None
    note = (
        "the first frame of an animated GIF — you're seeing only this one still frame, "
        "not the animation"
        if animated
        else "a GIF (a single still frame)"
    )
    return buf.getvalue(), note


async def download_images(message: discord.Message) -> list[tuple[bytes, str, str]]:
    """Read a message's image attachments as ``(data, mime, note)`` triples. ``note`` is
    normally '' but describes the medium when it isn't a plain still (e.g. a GIF the bot
    is reading a single frame of). GIFs are flattened to a first-frame PNG so vision can
    actually see them. Best-effort — a failed read or undecodable GIF is skipped."""
    out: list[tuple[bytes, str, str]] = []
    for att in image_attachments(message):
        try:
            data = await att.read()
        except Exception:
            log.debug("couldn't read attachment %s on message %s", att.filename, message.id)
            continue
        mime = att.content_type or "image/png"
        note = ""
        if mime == "image/gif" or (att.filename or "").lower().endswith(".gif"):
            frame = _gif_first_frame(data)
            if frame is None:
                continue  # unreadable GIF; skip rather than send bytes vision will reject
            data, note = frame
            mime = "image/png"
        out.append((data, mime, note))
    return out
