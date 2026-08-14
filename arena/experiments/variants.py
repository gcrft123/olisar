"""A variant: one complete, named, reproducible configuration of Olisar's behaviour.

Deliberately spans both halves of the prompt, because the two research questions the arena
exists to answer are different questions about the same system:

``prompt_overrides``  the **baked-in** layer — operating rules, tool briefing, proactive
                      note. Not operator-editable in the product. Improving this ships to
                      everyone, as a source change to ``olisar/persona.py`` and friends.
``persona``           the **operator-editable** layer — system prompt, tone notes. Improving
                      this ships as *advice*: the pattern library an operator writes their
                      own custom instructions from.

Holding one fixed while varying the other is what keeps the two answers separable. A round
that changes both and improves has learned nothing about either.

Applying a variant needs no restart: prompt overrides are re-read on mtime change, and
persona/config/proactivity are read from the database on every reply.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from arena.config import VARIANTS_DIR, ArenaConfig
from arena.control.dashboard import Dashboard

log = logging.getLogger("arena.variants")

BASELINE = "baseline"

OVERRIDE_KEYS = ("operating_rules", "tools_note", "proactive_note")


@dataclass
class Variant:
    name: str
    hypothesis: str = ""
    parent: str = BASELINE
    prompt_overrides: dict[str, str] = field(default_factory=dict)
    persona: dict[str, str] = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    proactivity: dict = field(default_factory=dict)

    @property
    def touches_baked_in(self) -> bool:
        return bool(self.prompt_overrides)

    @property
    def touches_operator_layer(self) -> bool:
        return bool(self.persona)

    def path(self) -> Path:
        return VARIANTS_DIR / f"{self.name}.json"

    def save(self) -> Path:
        VARIANTS_DIR.mkdir(parents=True, exist_ok=True)
        path = self.path()
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return path

    def describe(self) -> str:
        layers = []
        if self.touches_baked_in:
            layers.append("baked-in: " + ", ".join(sorted(self.prompt_overrides)))
        if self.touches_operator_layer:
            layers.append("persona: " + ", ".join(sorted(self.persona)))
        if self.config:
            layers.append("config: " + ", ".join(sorted(self.config)))
        if self.proactivity:
            layers.append("proactivity: " + ", ".join(sorted(self.proactivity)))
        return "; ".join(layers) or "(no changes — this is the stock configuration)"


def baseline() -> Variant:
    """Stock Olisar: no overrides, whatever persona the instance already has."""
    return Variant(name=BASELINE, hypothesis="stock configuration", parent="")


def load(name: str) -> Variant:
    if name == BASELINE and not (VARIANTS_DIR / f"{BASELINE}.json").is_file():
        return baseline()
    path = VARIANTS_DIR / f"{name}.json"
    if not path.is_file():
        known = ", ".join(sorted(p.stem for p in VARIANTS_DIR.glob("*.json"))) or "(none)"
        raise FileNotFoundError(f"unknown variant {name!r}. Known: {known}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    unknown = set(raw.get("prompt_overrides", {})) - set(OVERRIDE_KEYS)
    if unknown:
        raise ValueError(
            f"{name}: unknown prompt_overrides key(s) {', '.join(sorted(unknown))}. "
            f"Recognised: {', '.join(OVERRIDE_KEYS)}"
        )
    return Variant(**raw)


def load_all() -> dict[str, Variant]:
    variants = {BASELINE: baseline()}
    if VARIANTS_DIR.is_dir():
        for path in sorted(VARIANTS_DIR.glob("*.json")):
            try:
                variants[path.stem] = load(path.stem)
            except (ValueError, TypeError) as exc:
                log.warning("skipping malformed variant %s: %s", path.name, exc)
    return variants


def write_overrides(cfg: ArenaConfig, overrides: dict[str, str]) -> Path:
    """Write (or clear) the prompt-override file the instance reads.

    Always writes the file, even when empty: leaving a previous variant's overrides on disk
    is how a "baseline" run silently measures the last challenger instead.
    """
    path = cfg.prompt_overrides_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(overrides or {}, indent=2), encoding="utf-8")
    return path


async def apply(cfg: ArenaConfig, variant: Variant) -> dict:
    """Put the instance into this variant's configuration. No restart required.

    Returns what was actually changed, so a round report can state the configuration under
    test rather than the configuration that was requested.
    """
    applied: dict = {"variant": variant.name}
    applied["prompt_overrides"] = sorted(variant.prompt_overrides)
    write_overrides(cfg, variant.prompt_overrides)

    async with Dashboard(cfg) as dash:
        if variant.persona:
            await dash.set_persona(**variant.persona)
            applied["persona"] = sorted(variant.persona)
        if variant.config:
            await dash.set_config(**variant.config)
            applied["config"] = sorted(variant.config)
        if variant.proactivity:
            await dash.set_proactivity(**variant.proactivity)
            applied["proactivity"] = sorted(variant.proactivity)
    log.info("applied variant %s (%s)", variant.name, variant.describe())
    return applied


def current_baked_in() -> dict[str, str]:
    """The baked-in blocks as they exist in the source right now.

    The starting point for any proposal, and what a promoted variant is diffed against when
    it is time to land the change in ``olisar/``.
    """
    from bot.cogs import proactive  # noqa: F401  (ensures the module graph is importable)
    from olisar.persona import OPERATING_RULES
    from olisar.pipeline import TOOLS_NOTE
    from olisar.proactivity import PROACTIVE_NOTE

    return {
        "operating_rules": OPERATING_RULES,
        "tools_note": TOOLS_NOTE,
        "proactive_note": PROACTIVE_NOTE,
    }


SOURCE_OF = {
    "operating_rules": "olisar/persona.py :: OPERATING_RULES",
    "tools_note": "olisar/pipeline.py :: TOOLS_NOTE",
    "proactive_note": "olisar/proactivity.py :: PROACTIVE_NOTE",
}


def landing_instructions(variant: Variant) -> str:
    """Where a promoted variant's text has to be written to actually ship.

    An override file is a lab instrument. A variant that wins and stays in the file has
    improved nothing for anyone running Olisar.
    """
    if not variant.prompt_overrides:
        return (
            f"{variant.name} changes no baked-in blocks. Its persona text is operator "
            f"guidance — write it up rather than committing it."
        )

    lines = [f"To land {variant.name} in the product, replace:"]
    for key in sorted(variant.prompt_overrides):
        lines.append(f"  - {SOURCE_OF.get(key, key)}")
    lines.append(f"Then delete {variant.name} from arena/variants/ and re-run the red-team gate against the source.")
    return "\n".join(lines)
