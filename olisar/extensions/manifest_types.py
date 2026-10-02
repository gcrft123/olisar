"""Whether an extension's manifest has the types the bot and the console expect.

The manifest comes from running the extension's own code (``sandbox.extract_manifest``),
and that code can replace the bootstrap's ``__collectManifest``, so nothing about its
shape is guaranteed. The console renders its names, labels and descriptions as text: an
object there (a settings field with ``label: {}``, a command named ``{ x: 1 }``) threw on
every render and took the whole Extensions page down, with no way left to turn the
extension off or remove it. The bot indexes into its lists the same way. Installing,
importing or saving an extension whose manifest has the wrong types is refused.

Problems are reported in the SDK's own field names (``settingsSchema``, ``systemNote``),
since that's what the author wrote.
"""

from __future__ import annotations

import logging

log = logging.getLogger("olisar.extensions.manifest_types")


def problems(manifest: dict) -> list[str]:
    """What's wrong with ``manifest``'s types, one line each; empty when nothing is. A
    missing optional field is fine: everything that reads one has a default."""
    out: list[str] = []

    def text(obj: dict, key: str, where: str, *, nullable: bool = False) -> None:
        if key not in obj or (nullable and obj[key] is None):
            return
        if not isinstance(obj[key], str):
            out.append(f"{where} must be a string")

    def texts(obj: dict, key: str, where: str) -> None:
        value = obj.get(key)
        if value is None:
            return
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            out.append(f"{where} must be a list of strings")

    def objects(value: object, where: str) -> list[tuple[str, dict]]:
        """The entries of a list of objects, each with its path; anything else is noted."""
        if value is None:
            return []
        if not isinstance(value, list):
            out.append(f"{where} must be a list")
            return []
        good = []
        for i, item in enumerate(value):
            if isinstance(item, dict):
                good.append((f"{where}[{i}]", item))
            else:
                out.append(f"{where}[{i}] must be an object")
        return good

    for key, where in (("name", "name"), ("version", "version"), ("category", "category"),
                       ("description", "description"), ("system_note", "systemNote")):
        text(manifest, key, where)
    texts(manifest, "permissions", "permissions")
    texts(manifest, "component_handlers", "components")
    texts(manifest, "event_handlers", "events")

    for where, tool in objects(manifest.get("tools"), "tools"):
        text(tool, "name", f"{where}.name")
        text(tool, "description", f"{where}.description")
        if tool.get("parameters") is not None and not isinstance(tool["parameters"], dict):
            out.append(f"{where}.parameters must be an object")

    for where, cmd in objects(manifest.get("commands"), "commands"):
        text(cmd, "name", f"{where}.name")
        text(cmd, "description", f"{where}.description")
        text(cmd, "defaultMemberPermissions", f"{where}.defaultMemberPermissions", nullable=True)
        for owhere, opt in objects(cmd.get("options"), f"{where}.options"):
            if not isinstance(opt.get("name"), str):
                out.append(f"{owhere}.name must be a string")
            text(opt, "description", f"{owhere}.description", nullable=True)
            text(opt, "type", f"{owhere}.type", nullable=True)

    schema = manifest.get("settings_schema")
    if schema is not None and not isinstance(schema, dict):
        out.append("settingsSchema must be an object")
    elif schema is not None:
        for where, field in objects(schema.get("fields"), "settingsSchema.fields"):
            if not isinstance(field.get("key"), str):
                out.append(f"{where}.key must be a string")
            text(field, "type", f"{where}.type")
            text(field, "label", f"{where}.label", nullable=True)
            text(field, "desc", f"{where}.desc", nullable=True)

    seeds = manifest.get("seeds")
    if seeds is not None and not isinstance(seeds, dict):
        out.append("seeds must be an object")
    elif seeds is not None:
        for key in ("kbSources", "kb_sources", "glossary"):
            objects(seeds.get(key), f"seeds.{key}")
    return out


# ── Reading a manifest stored before these checks existed ────────────────────────
# An extension installed earlier can still carry any of the above. What lists extensions
# for the console reads its manifest through these, which keep what's usable, turn stray
# values into text and leave the rest out. One bad row must not fail the whole listing:
# the operator has to see the extension to turn it off or delete it.

# Extension keys whose stored manifest has been checked. The catalog reloads every few
# seconds, and a manifest saved since these checks existed can't fail them, so once per
# extension is enough.
_checked: set[str] = set()


def report_stored(key: str, manifest: object) -> None:
    """Log, once per extension, that its stored manifest has the wrong types."""
    if key in _checked:
        return
    _checked.add(key)
    found = problems(manifest) if isinstance(manifest, dict) else ["the manifest must be an object"]
    if found:
        log.warning(
            "extension %s has a stored manifest with the wrong types (%s); showing what's usable",
            key, "; ".join(found[:5]),
        )


def text(value: object) -> str:
    """``value`` as text to show: a string as it is, nothing as empty, anything else as
    its ``str()``."""
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def objects_in(value: object) -> list[dict]:
    """The object entries of what should be a list of objects."""
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def settings_schema(manifest: dict) -> dict | None:
    """The settings form to show: the fields that have a key, with their text as text."""
    schema = manifest.get("settings_schema")
    if not isinstance(schema, dict):
        return None
    fields = []
    for field in objects_in(schema.get("fields")):
        if not isinstance(field.get("key"), str) or not field["key"]:
            continue
        fields.append({
            **field,
            **{k: text(field[k]) for k in ("type", "label", "desc") if field.get(k) is not None},
        })
    return {**schema, "fields": fields}


__all__ = ["objects_in", "problems", "report_stored", "settings_schema", "text"]
