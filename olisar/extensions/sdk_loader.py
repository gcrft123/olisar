"""Turn a stored ``ExtensionPackage`` into a runtime ``Extension``.

The package's declarative manifest gives us the tool/command schemas and seeds; the
handlers are thin closures that run the package's compiled JS in the sandbox. The
result is an ordinary ``Extension``, so it merges into the same catalog the built-in
Python extensions use and flows through the unchanged pipeline.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from google.genai import types
from sqlalchemy import select

from olisar import sandbox
from olisar.db.models import KBSource, KBSourceType, KBStatus
from olisar.extensions.base import Extension, ExtensionTool
from olisar.memory.facts import upsert_facts

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from olisar.db.models import ExtensionPackage

log = logging.getLogger("olisar.extensions.sdk_loader")

_JSON_TYPE = {
    "string": types.Type.STRING, "number": types.Type.NUMBER, "integer": types.Type.INTEGER,
    "boolean": types.Type.BOOLEAN, "array": types.Type.ARRAY, "object": types.Type.OBJECT,
}


def schema_from_jsonschema(js: dict | None) -> types.Schema:
    """Convert a JSON-schema fragment (as authored in the SDK) into a Gemini
    ``types.Schema``. Mirrors the ``_str``/``_obj`` helpers used by the core tools."""
    js = js or {}
    t = _JSON_TYPE.get(str(js.get("type", "object")).lower(), types.Type.OBJECT)
    kwargs: dict = {"type": t}
    if js.get("description"):
        kwargs["description"] = str(js["description"])
    if t == types.Type.OBJECT:
        props = js.get("properties") or {}
        kwargs["properties"] = {k: schema_from_jsonschema(v) for k, v in props.items()}
        if js.get("required"):
            kwargs["required"] = [str(x) for x in js["required"]]
    if t == types.Type.ARRAY and js.get("items"):
        kwargs["items"] = schema_from_jsonschema(js["items"])
    if js.get("enum"):
        kwargs["enum"] = [str(x) for x in js["enum"]]
    return types.Schema(**kwargs)


def _make_tool_handler(key: str, compiled_js: str, perms: list[str], tool_name: str, trusted: bool):
    async def handler(args: dict, ctx) -> str:
        return await sandbox.run_tool(
            ext_key=key, compiled_js=compiled_js, permissions=perms,
            tool_name=tool_name, args=args, ctx=ctx, trusted=trusted,
        )
    return handler


async def _apply_seeds(session: "AsyncSession", guild_id: int, seeds: dict) -> None:
    """Apply the manifest's declarative seeds idempotently (so no-code extensions can
    seed too). Mirrors star_citizen._seed_kb."""
    for src in seeds.get("kbSources") or seeds.get("kb_sources") or []:
        uri = str(src.get("uri") or "").strip()
        if not uri:
            continue
        existing = await session.scalar(
            select(KBSource).where(KBSource.guild_id == guild_id, KBSource.uri == uri)
        )
        if existing is not None:
            continue
        kind = str(src.get("type") or "url").lower()
        session.add(KBSource(
            guild_id=guild_id,
            type=KBSourceType.website if kind == "website" else KBSourceType.url,
            uri=uri, title=str(src.get("title") or uri)[:200], status=KBStatus.pending,
        ))
    glossary = seeds.get("glossary") or []
    if glossary:
        await upsert_facts(
            session, guild_id=guild_id, channel_id=None,
            items=[{"subject": g.get("subject"), "fact": g.get("fact")} for g in glossary],
        )


def _make_on_enable(key: str, compiled_js: str, perms: list[str], seeds: dict, has_js: bool, trusted: bool):
    async def on_enable(session: "AsyncSession", guild_id: int) -> None:
        await _apply_seeds(session, guild_id, seeds)
        if has_js:
            await sandbox.run_on_enable(
                ext_key=key, compiled_js=compiled_js, permissions=perms,
                session=session, guild_id=guild_id, trusted=trusted,
            )
    return on_enable


def build_extension(pkg: "ExtensionPackage") -> Extension:
    """Build a runtime Extension from a stored package (no JS runs here — just
    closures + schema conversion)."""
    manifest = pkg.manifest or {}
    perms = list(pkg.permissions or [])
    # First-party (built-in / locally-authored) extensions are trusted with host secrets;
    # imported/marketplace ones are not (see capabilities._secret).
    trusted = (getattr(pkg, "origin", None) or "local") == "local"
    tools = tuple(
        ExtensionTool(
            declaration=types.FunctionDeclaration(
                name=t["name"], description=t.get("description", ""),
                parameters=schema_from_jsonschema(t.get("parameters")),
            ),
            handler=_make_tool_handler(pkg.key, pkg.compiled_js, perms, t["name"], trusted),
        )
        for t in manifest.get("tools", [])
        if t.get("name")
    )
    seeds = manifest.get("seeds") or {}
    has_js = bool(manifest.get("has_on_enable"))
    needs_enable = has_js or bool(seeds.get("kbSources") or seeds.get("kb_sources") or seeds.get("glossary"))
    return Extension(
        key=pkg.key,
        name=pkg.name or manifest.get("name", pkg.key),
        description=pkg.description or manifest.get("description", ""),
        category=pkg.category or manifest.get("category", "General"),
        default_enabled=bool(manifest.get("default_enabled")),
        tools=tools,
        system_note=manifest.get("system_note", ""),
        on_enable=_make_on_enable(pkg.key, pkg.compiled_js, perms, seeds, has_js, trusted) if needs_enable else None,
    )
