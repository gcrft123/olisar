"""Built-in example extensions — a small spread that exercises the framework:
a tool that's contextual (dice), a pure-compute tool (calculator), and a
behaviour-only extension that just adds a system note (concise mode).

All are safe and self-contained (no external or paid APIs), and all ship disabled
by default — admins opt in from the dashboard.
"""

from __future__ import annotations

import ast
import operator
import random
import re

from google.genai import types

from olisar.extensions.base import Extension, ExtensionTool, register


def _str(desc: str) -> types.Schema:
    return types.Schema(type=types.Type.STRING, description=desc)


def _obj(props: dict, required: list[str]) -> types.Schema:
    return types.Schema(type=types.Type.OBJECT, properties=props, required=required)


# ── Dice ────────────────────────────────────────────────────────────────────
_DICE_RE = re.compile(r"(\d*)d(\d+)([+-]\d+)?")


def _roll(notation: str) -> str | None:
    m = _DICE_RE.fullmatch(notation.replace(" ", "").lower())
    if not m:
        return None
    count = int(m.group(1) or 1)
    sides = int(m.group(2))
    mod = int(m.group(3) or 0)
    if not (1 <= count <= 100) or not (2 <= sides <= 1000):
        return None
    rolls = [random.randint(1, sides) for _ in range(count)]
    detail = " + ".join(map(str, rolls))
    if mod:
        detail += f" {'+' if mod > 0 else '-'} {abs(mod)}"
    return f"{notation} → [{detail}] = {sum(rolls) + mod}"


async def _dice_handler(args: dict, ctx) -> str:
    result = _roll(args.get("notation") or "1d6")
    return result or "I can roll dice like 1d20 or 2d6+3 (≤100 dice, ≤1000 sides)."


# ── Calculator (safe arithmetic, no eval) ───────────────────────────────────
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _safe_eval(node):
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.operand))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
        base, exp = _safe_eval(node.left), _safe_eval(node.right)
        if abs(exp) > 256:  # guard against absurdly large results
            raise ValueError("exponent too large")
        return base ** exp
    raise ValueError("unsupported expression")


async def _calc_handler(args: dict, ctx) -> str:
    expr = (args.get("expression") or "").strip()
    try:
        value = _safe_eval(ast.parse(expr, mode="eval"))
    except Exception:
        return f"I couldn't compute {expr!r} — I only do plain arithmetic (+ - * / % ** and parentheses)."
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{expr} = {value}"


def register_builtins() -> None:
    """Register the built-in extensions. Idempotent-safe to call once at import."""
    register(Extension(
        key="dice",
        name="Dice roller",
        description="Olisar can roll dice on request — e.g. \"roll 2d6+3\".",
        category="Fun",
        tools=(ExtensionTool(
            declaration=types.FunctionDeclaration(
                name="roll_dice",
                description=(
                    "Roll dice in standard notation (e.g. 1d20, 2d6+3) and return the "
                    "result. Use when someone asks you to roll dice or flip for it."
                ),
                parameters=_obj({"notation": _str("dice notation, e.g. 2d6+3")}, ["notation"]),
            ),
            handler=_dice_handler,
        ),),
    ))
    register(Extension(
        key="calculator",
        name="Calculator",
        description="Olisar can do exact arithmetic instead of guessing at numbers.",
        category="Utility",
        tools=(ExtensionTool(
            declaration=types.FunctionDeclaration(
                name="calculate",
                description=(
                    "Evaluate a plain arithmetic expression exactly (+, -, *, /, %, **, "
                    "parentheses). Use for any math so you don't miscompute."
                ),
                parameters=_obj({"expression": _str("an arithmetic expression")}, ["expression"]),
            ),
            handler=_calc_handler,
        ),),
    ))
    register(Extension(
        key="concise_mode",
        name="Concise mode",
        description="Olisar keeps replies short and to the point.",
        category="Behavior",
        system_note=(
            "Keep your replies short and to the point — a sentence or two unless the "
            "question genuinely needs more."
        ),
    ))
