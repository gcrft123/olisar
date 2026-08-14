"""Emulated-member personas: who each bot in the fleet is pretending to be.

A persona is a JSON file in ``arena/personas/``. The point of the fleet is *variety* —
Olisar sounding natural against one articulate, well-punctuated interlocutor proves very
little, because that's the easy case and it's the case a chatbot already handles. The
personas that earn their place are the ones that break assumptions: someone who types three
fragments in a row, someone whose English is a second language, someone who is mildly
hostile, someone who never asks a question but clearly wants help.

``key`` must match the ``ARENA_BOT_TOKEN_<KEY>`` env suffix, and is the join between a
persona file and the Discord application that speaks for it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from arena.config import PERSONAS_DIR


@dataclass(frozen=True)
class Persona:
    key: str
    display_name: str
    blurb: str
    voice: str
    traits: list[str] = field(default_factory=list)
    # How likely this member is to keep talking when nobody prompted them, 0..1. The
    # scenario decides who speaks, but ties are broken by chattiness so a run doesn't
    # come out perfectly round-robin — real channels are lopsided.
    chattiness: float = 0.5
    # Emulators default to NOT reacting to Olisar unprompted. A fleet that always
    # answers the bot produces an infinite, entirely artificial two-party loop, which
    # burns quota and teaches nothing. Scenarios turn this on where it's the point.
    reacts_to_olisar: bool = False

    @property
    def token_env(self) -> str:
        return f"ARENA_BOT_TOKEN_{self.key.upper()}"

    def brief(self) -> str:
        """The persona as the dialogue model sees it."""
        traits = ", ".join(self.traits) if self.traits else "—"
        return (
            f"You are '{self.display_name}', a member of this Discord server.\n"
            f"Who you are: {self.blurb}\n"
            f"How you type: {self.voice}\n"
            f"Traits: {traits}"
        )


class PersonaError(RuntimeError):
    pass


def _parse(path: Path) -> Persona:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PersonaError(f"{path.name}: {exc}") from exc
    missing = [f for f in ("key", "display_name", "blurb", "voice") if not raw.get(f)]
    if missing:
        raise PersonaError(f"{path.name}: missing required field(s): {', '.join(missing)}")
    return Persona(
        key=str(raw["key"]).strip().lower(),
        display_name=str(raw["display_name"]),
        blurb=str(raw["blurb"]),
        voice=str(raw["voice"]),
        traits=[str(t) for t in raw.get("traits", [])],
        chattiness=float(raw.get("chattiness", 0.5)),
        reacts_to_olisar=bool(raw.get("reacts_to_olisar", False)),
    )


def load_all(directory: Path | None = None) -> dict[str, Persona]:
    """Every persona on disk, keyed by ``key``. Raises on a duplicate key, which would
    otherwise mean one persona file silently shadows another."""
    directory = directory or PERSONAS_DIR
    personas: dict[str, Persona] = {}
    for path in sorted(directory.glob("*.json")):
        persona = _parse(path)
        if persona.key in personas:
            raise PersonaError(f"duplicate persona key {persona.key!r} in {path.name}")
        personas[persona.key] = persona
    return personas


def load(key: str, directory: Path | None = None) -> Persona:
    personas = load_all(directory)
    if key not in personas:
        known = ", ".join(sorted(personas)) or "(none)"
        raise PersonaError(f"unknown persona {key!r}. Known: {known}")
    return personas[key]
