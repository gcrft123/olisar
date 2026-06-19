"""Star Citizen extension — togglable package of SC features.

Contributes (when enabled):
* tools — Executive Hangar status, ship-matrix lookup, and UEX commodity/vehicle/
  location lookups (all live HTTP, best-effort, no paid APIs);
* an ``on_enable`` hook that seeds the RSI Comm-Link page into the knowledge base;
* ``fetch_citizen`` — an RSI citizen-profile scraper used by the ``/citizen`` cog.

Deliberately discord-agnostic (no ``discord`` import) so both the bot and the API
process can import it. Every network call degrades to a friendly string/None on
failure — SC sites are scraped/undocumented and can change or block.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from difflib import get_close_matches
from urllib.parse import quote, urljoin

import httpx
from bs4 import BeautifulSoup
from google.genai import types
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from olisar import runtime_keys
from olisar.db.models import KBSource, KBSourceType, KBStatus
from olisar.extensions.base import Extension, ExtensionTool, register

log = logging.getLogger("olisar.ext.star_citizen")

_UA = {"User-Agent": "OlisarBot/1.0 (Discord community bot; Star Citizen extension)"}
_RSI = "https://robertsspaceindustries.com"
_CITIZEN_BASE = f"{_RSI}/en/citizens/"
_COMM_LINK_URL = f"{_RSI}/en/comm-link/"
_SHIP_MATRIX = f"{_RSI}/ship-matrix/index"
_EXEC_URL = "https://exec.xyxyll.com/"
_EXEC_JS = "https://exec.xyxyll.com/app.js"
_UEX_BASE = "https://api.uexcorp.uk/2.0/"


def _str(desc: str) -> types.Schema:
    return types.Schema(type=types.Type.STRING, description=desc)


def _obj(props: dict, required: list[str]) -> types.Schema:
    return types.Schema(type=types.Type.OBJECT, properties=props, required=required)


# ── Executive Hangar status (exec.xyxyll.com) ───────────────────────────────
# The countdown is computed in app.js from an anchor + cycle constants. We refetch
# app.js so we always use the author's current re-sync, then replicate the math.
async def _hangar_status(args: dict, ctx) -> str:
    try:
        async with httpx.AsyncClient(headers=_UA, timeout=15.0, follow_redirects=True) as c:
            js = (await c.get(_EXEC_JS)).text
            page = (await c.get(_EXEC_URL)).text
    except Exception:
        return "Couldn't reach the Executive Hangar tracker right now."

    state = ""
    try:
        anchor = re.search(r"INITIAL_OPEN_TIME\s*=\s*new Date\('([^']+)'\)", js).group(1)
        drift = int(re.search(r"CYCLE_DRIFT_MS\s*=\s*(\d+)", js).group(1))
        online_min = int(re.search(r"DESIGN_ONLINE_MIN\s*=\s*(\d+)", js).group(1))
        offline_min = int(re.search(r"DESIGN_OFFLINE_MIN\s*=\s*(\d+)", js).group(1))
        anchor_dt = datetime.fromisoformat(anchor)
        online_ms = online_min * 60_000
        cycle_ms = (online_min + offline_min) * 60_000 + drift
        open_dur = round(cycle_ms * online_ms / ((online_min + offline_min) * 60_000))
        elapsed = (datetime.now(timezone.utc) - anchor_dt).total_seconds() * 1000
        in_cycle = elapsed % cycle_ms
        if in_cycle < open_dur:
            status, remaining = "OPEN", open_dur - in_cycle
        else:
            status, remaining = "CLOSED", cycle_ms - in_cycle
        mins, secs = int(remaining // 60_000), int((remaining % 60_000) // 1000)
        state = f"The Pyro Executive Hangar is currently **{status}** — next change in ~{mins}m {secs}s."
    except Exception:
        log.exception("hangar cycle computation failed")
        state = "Executive Hangar tracker reached, but I couldn't compute the live timer."

    patch = re.search(r"Patch\s+([0-9][^<\s]*)", page)
    if patch:
        state += f" (Star Citizen {patch.group(1)})"
    return state


# ── Ship matrix (RSI) ────────────────────────────────────────────────────────
async def _ship_lookup(args: dict, ctx) -> str:
    name = (args.get("name") or "").strip()
    if not name:
        return "Give me a ship name to look up."
    try:
        async with httpx.AsyncClient(headers=_UA, timeout=20.0, follow_redirects=True) as c:
            data = (await c.get(_SHIP_MATRIX)).json().get("data", [])
    except Exception:
        return "Couldn't reach the RSI ship matrix right now."
    nl = name.lower()
    matches = [s for s in data if nl in (s.get("name") or "").lower()]
    if not matches:
        return f"No ship matching “{name}” in the RSI ship matrix."
    matches.sort(key=lambda s: len(s.get("name") or ""))
    s = matches[0]
    man = s.get("manufacturer") if isinstance(s.get("manufacturer"), dict) else {}
    header = f"**{s.get('name')}** — {man.get('name') or 'Unknown manufacturer'}"
    bits = []
    if s.get("focus"):
        bits.append(f"role: {s['focus']}")
    if s.get("size"):
        bits.append(f"size: {s['size']}")
    if s.get("min_crew") or s.get("max_crew"):
        lo, hi = s.get("min_crew"), s.get("max_crew")
        bits.append(f"crew: {lo}–{hi}" if lo != hi else f"crew: {hi}")
    if s.get("cargocapacity") is not None:
        bits.append(f"cargo: {s['cargocapacity']} SCU")
    if s.get("scm_speed"):
        bits.append(f"SCM speed: {s['scm_speed']} m/s")
    if s.get("production_status"):
        bits.append(f"status: {s['production_status']}")
    out = header + ("\n" + " · ".join(bits) if bits else "")
    if len(matches) > 1:
        out += f"\n(+{len(matches) - 1} other name matches)"
    return out


# ── UEX (uexcorp) ─────────────────────────────────────────────────────────────
async def _uex_get(path: str, params: dict | None = None):
    """GET a UEX endpoint. Returns (data_list, error_str). Token is optional."""
    headers = dict(_UA)
    uex_key = await runtime_keys.uex_api_key()
    if uex_key:
        headers["Authorization"] = f"Bearer {uex_key}"
    try:
        async with httpx.AsyncClient(headers=headers, timeout=20.0, follow_redirects=True) as c:
            r = await c.get(_UEX_BASE + path, params=params or {})
    except Exception:
        return None, "Couldn't reach UEX right now."
    if r.status_code == 401:
        return None, "That UEX endpoint needs a token — set UEX_API_KEY."
    try:
        body = r.json()
    except Exception:
        return None, "UEX returned an unexpected response."
    if body.get("status") != "ok":
        return None, f"UEX error: {body.get('status')}"
    return body.get("data") or [], None


def _norm(value) -> str:
    """Lowercase, alphanumeric-only — so 'Area18' matches 'Area 18', 'CRU-L1'
    matches 'CRUL1', etc."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _best_match(rows: list, query: str, *keys: str):
    """Find the best row by a normalized substring match on any key, falling back
    to a fuzzy (typo-tolerant) match on the first key — e.g. user 'Quantanium'
    resolves to UEX's 'Quantainium'."""
    nq = _norm(query)
    if not nq:
        return None
    hits = [r for r in rows if any(nq in _norm(r.get(k)) for k in keys)]
    if hits:
        hits.sort(key=lambda r: len(str(r.get(keys[0]) or "")))
        return hits[0]
    names = {str(r.get(keys[0]) or ""): r for r in rows if r.get(keys[0])}
    close = get_close_matches(query.lower(), [n.lower() for n in names], n=1, cutoff=0.7)
    if close:
        return next((r for n, r in names.items() if n.lower() == close[0]), None)
    return None


async def _uex_commodity(args: dict, ctx) -> str:
    q = (args.get("name") or "").strip()
    if not q:
        return "Give me a commodity name."
    data, err = await _uex_get("commodities")
    if err:
        return err
    c = _best_match(data, q, "name", "code")
    if not c:
        return f"No commodity matching “{q}” on UEX."
    parts = [f"**{c.get('name')}** ({c.get('code')}) — {c.get('kind') or 'commodity'}"]
    if c.get("price_buy"):
        parts.append(f"avg buy {int(c['price_buy']):,} aUEC")
    if c.get("price_sell"):
        parts.append(f"avg sell {int(c['price_sell']):,} aUEC")
    parts.append("tradeable now" if c.get("is_available") else "not currently traded")
    return " · ".join(parts)


async def _uex_vehicle(args: dict, ctx) -> str:
    q = (args.get("name") or "").strip()
    if not q:
        return "Give me a ship/vehicle name."
    data, err = await _uex_get("vehicles")
    if err:
        return err
    v = _best_match(data, q, "name_full", "name", "slug")
    if not v:
        return f"No vehicle matching “{q}” on UEX."
    parts = [f"**{v.get('name_full') or v.get('name')}**"]
    if v.get("scu") is not None:
        parts.append(f"{v['scu']} SCU cargo")
    if v.get("crew"):
        parts.append(f"crew {v['crew']}")
    if v.get("is_concept"):
        parts.append("concept (not flyable)")
    return " · ".join(parts)


# Searched in order, so a landing-zone name resolves to the place before a shop.
_LOCATION_SOURCES = [
    ("cities", "city"),
    ("space_stations", "station"),
    ("outposts", "outpost"),
    ("terminals", "terminal"),
]


async def _uex_location(args: dict, ctx) -> str:
    q = (args.get("name") or "").strip()
    if not q:
        return "Give me a station/terminal/location name."
    last_err = None
    for path, label in _LOCATION_SOURCES:
        data, err = await _uex_get(path)
        if err:
            last_err = err
            continue
        loc = _best_match(data, q, "name", "nickname", "code")
        if loc:
            out = f"**{loc.get('name')}** ({loc.get('type') or label})"
            if loc.get("nickname") and loc["nickname"] != loc.get("name"):
                out += f" — {loc['nickname']}"
            return out
    return last_err or f"No location matching “{q}” on UEX."


def _auec(value) -> str | None:
    """Format a number as 'N,NNN aUEC', or None if it isn't a usable number."""
    try:
        return f"{int(float(value)):,} aUEC"
    except (TypeError, ValueError):
        return None


async def _resolve(path: str, query: str, *keys: str):
    """Fetch a UEX list endpoint and best-match a row by name. Returns (row, error)."""
    data, err = await _uex_get(path)
    if err:
        return None, err
    return _best_match(data, query, *keys), None


async def _uex_commodity_price(args: dict, ctx) -> str:
    q = (args.get("name") or "").strip()
    if not q:
        return "Give me a commodity name."
    c, err = await _resolve("commodities", q, "name", "code")
    if err:
        return err
    if not c:
        return f"No commodity matching “{q}” on UEX."
    rows, err = await _uex_get("commodities_prices", {"id_commodity": c.get("id")})
    if err:
        return err
    if not rows:
        return f"No live terminal prices for **{c.get('name')}** on UEX right now."
    sells = [r for r in rows if (r.get("price_sell") or 0) > 0]
    buys = [r for r in rows if (r.get("price_buy") or 0) > 0]
    parts = [f"**{c.get('name')}** live prices ({len(rows)} terminal(s))"]
    if sells:
        best = max(sells, key=lambda r: r["price_sell"])
        parts.append(f"best sell {_auec(best['price_sell'])} at {best.get('terminal_name') or '?'}")
    if buys:
        best = min(buys, key=lambda r: r["price_buy"])
        parts.append(f"best buy {_auec(best['price_buy'])} at {best.get('terminal_name') or '?'}")
    if not sells and not buys:
        parts.append("listed, but no active buy/sell prices")
    return " · ".join(parts)


async def _uex_commodity_ranking(args: dict, ctx) -> str:
    rows, err = await _uex_get("commodities_ranking")
    if err:
        return err
    rows = [r for r in (rows or []) if r.get("name")]
    rows.sort(key=lambda r: r.get("profitability") or 0, reverse=True)
    if rows:
        lines = ["Top UEX commodities by profit per SCU:"]
        for i, r in enumerate(rows[:5], 1):
            lines.append(f"{i}. **{r['name']}** — {_auec(r.get('profitability')) or '?'}/SCU")
        return "\n".join(lines)
    # /commodities_ranking is deprecated and usually empty — derive a ranking from
    # the average buy→sell margin in /commodities instead.
    comm, err = await _uex_get("commodities")
    if err:
        return err
    ranked = sorted(
        ((sell - buy, c["name"]) for c in (comm or [])
         if c.get("name") and (buy := c.get("price_buy") or 0) > 0 and (sell := c.get("price_sell") or 0) > buy),
        reverse=True,
    )
    if not ranked:
        return "UEX returned no commodity ranking."
    lines = ["Top UEX commodities by average buy→sell margin:"]
    for i, (margin, name) in enumerate(ranked[:5], 1):
        lines.append(f"{i}. **{name}** — ~{_auec(margin)}/SCU")
    return "\n".join(lines)


async def _uex_commodity_route(args: dict, ctx) -> str:
    commodity = (args.get("commodity") or "").strip()
    origin = (args.get("origin") or "").strip()
    if not commodity and not origin:
        return "Give me a commodity and/or an origin terminal to find trade routes."
    params: dict = {}
    label: list[str] = []
    if commodity:
        c, err = await _resolve("commodities", commodity, "name", "code")
        if err:
            return err
        if not c:
            return f"No commodity matching “{commodity}” on UEX."
        params["id_commodity"] = c.get("id")
        label.append(str(c.get("name")))
    if origin:
        t, err = await _resolve("terminals", origin, "name", "nickname", "code")
        if err:
            return err
        if not t:
            return f"No origin terminal matching “{origin}” on UEX."
        params["id_terminal_origin"] = t.get("id")
        label.append(f"from {t.get('name')}")
    rows, err = await _uex_get("commodities_routes", params)
    if err:
        return err
    if not rows:
        return f"No profitable routes for {' '.join(label)} on UEX right now."
    rows.sort(key=lambda r: r.get("profit") or 0, reverse=True)
    lines = [f"Top trade routes ({' '.join(label)}):"]
    for i, r in enumerate(rows[:3], 1):
        bits = [f"{r.get('origin_terminal_name') or '?'} → {r.get('destination_terminal_name') or '?'}"]
        if r.get("commodity_name") and not commodity:
            bits.append(r["commodity_name"])
        if r.get("price_margin"):
            bits.append(f"{_auec(r['price_margin'])}/SCU")
        if r.get("profit"):
            bits.append(f"{_auec(r['profit'])} total")
        if r.get("scu_margin"):
            bits.append(f"{int(r['scu_margin'])} SCU")
        lines.append(f"{i}. " + " · ".join(b for b in bits if b))
    return "\n".join(lines)


async def _uex_commodity_status(args: dict, ctx) -> str:
    data, err = await _uex_get("commodities_status")
    if err:
        return err
    # API returns {"buy": [...], "sell": [...]}; the bands match, so show one side.
    rows = (data.get("sell") or data.get("buy")) if isinstance(data, dict) else data
    rows = list(rows or [])
    if not rows:
        return "UEX returned no inventory status levels."
    rows.sort(key=lambda r: r.get("percentage_start") or 0)
    lines = ["UEX terminal inventory levels (what the stock labels mean):"]
    for r in rows:
        name = r.get("name") or r.get("name_short") or r.get("code")
        lines.append(f"- **{name}**" + (f" — {r['percentage']}" if r.get("percentage") else ""))
    return "\n".join(lines)


async def _uex_vehicle_price(args: dict, ctx) -> str:
    q = (args.get("name") or "").strip()
    if not q:
        return "Give me a ship/vehicle name."
    v, err = await _resolve("vehicles", q, "name_full", "name", "slug")
    if err:
        return err
    if not v:
        return f"No vehicle matching “{q}” on UEX."
    name = v.get("name_full") or v.get("name")
    rows, err = await _uex_get("vehicles_prices", {"id_vehicle": v.get("id")})
    if err:
        return err
    if not rows:
        return f"No pledge-store price listed for **{name}** on UEX."
    r = rows[0]
    cur = r.get("currency") or "USD"
    parts = [f"**{name}** — pledge store"]
    if r.get("price"):
        parts.append(f"{r['price']} {cur} standalone")
    if r.get("price_warbond"):
        parts.append(f"{r['price_warbond']} {cur} warbond")
    if r.get("on_sale"):
        parts.append("on sale now")
    return " · ".join(parts) + " (real money)"


async def _uex_vehicle_purchase(args: dict, ctx) -> str:
    q = (args.get("name") or "").strip()
    if not q:
        return "Give me a ship/vehicle name."
    v, err = await _resolve("vehicles", q, "name_full", "name", "slug")
    if err:
        return err
    if not v:
        return f"No vehicle matching “{q}” on UEX."
    name = v.get("name_full") or v.get("name")
    rows, err = await _uex_get("vehicles_purchases_prices", {"id_vehicle": v.get("id")})
    if err:
        return err
    buys = [r for r in (rows or []) if (r.get("price_buy") or 0) > 0]
    if not buys:
        return f"No in-game (aUEC) purchase location for **{name}** on UEX — it may be pledge-only."
    buys.sort(key=lambda r: r["price_buy"])
    best = buys[0]
    where = best.get("terminal_name") or best.get("city_name") or best.get("outpost_name") or "?"
    out = f"**{name}** — cheapest in-game buy {_auec(best['price_buy'])} at {where}"
    if len(buys) > 1:
        out += f" (+{len(buys) - 1} other location{'s' if len(buys) > 2 else ''})"
    return out


async def _uex_star_system(args: dict, ctx) -> str:
    q = (args.get("name") or "").strip()
    data, err = await _uex_get("star_systems")
    if err:
        return err
    if not data:
        return "UEX lists no star systems."
    if not q:
        live = sorted(s.get("name") for s in data if s.get("is_available_live") and s.get("name"))
        return ("Star systems playable now: " + ", ".join(live)) if live else "No live star systems."
    s = _best_match(data, q, "name", "code")
    if not s:
        return f"No star system matching “{q}” on UEX."
    parts = [f"**{s.get('name')}** system"]
    if s.get("faction_name"):
        parts.append(f"controlled by {s['faction_name']}")
    if s.get("jurisdiction_name"):
        parts.append(s["jurisdiction_name"])
    parts.append("playable now" if s.get("is_available_live") else "not yet in-game")
    return " · ".join(parts)


async def _uex_planet(args: dict, ctx) -> str:
    q = (args.get("name") or "").strip()
    if not q:
        return "Give me a planet name."
    data, err = await _uex_get("planets")
    if err:
        return err
    p = _best_match(data, q, "name", "name_origin", "code")
    if not p:
        return f"No planet matching “{q}” on UEX."
    parts = [f"**{p.get('name')}**"]
    if p.get("star_system_name"):
        parts.append(f"in {p['star_system_name']}")
    if p.get("faction_name"):
        parts.append(f"faction {p['faction_name']}")
    parts.append("in-game" if p.get("is_available_live") else "not yet in-game")
    return " · ".join(parts)


async def _uex_moon(args: dict, ctx) -> str:
    q = (args.get("name") or "").strip()
    if not q:
        return "Give me a moon name."
    data, err = await _uex_get("moons")
    if err:
        return err
    m = _best_match(data, q, "name", "name_origin", "code")
    if not m:
        return f"No moon matching “{q}” on UEX."
    parts = [f"**{m.get('name')}**"]
    if m.get("planet_name"):
        parts.append(f"orbiting {m['planet_name']}")
    if m.get("star_system_name"):
        parts.append(f"in {m['star_system_name']}")
    parts.append("in-game" if m.get("is_available_live") else "not yet in-game")
    return " · ".join(parts)


async def _uex_orbit(args: dict, ctx) -> str:
    q = (args.get("name") or "").strip()
    if not q:
        return "Give me an orbit or Lagrange-point name."
    data, err = await _uex_get("orbits")
    if err:
        return err
    o = _best_match(data, q, "name", "name_origin", "code")
    if not o:
        return f"No orbit matching “{q}” on UEX."
    parts = [f"**{o.get('name')}**"]
    if o.get("star_system_name"):
        parts.append(f"in {o['star_system_name']}")
    kind = next(
        (label for key, label in (
            ("is_lagrange", "Lagrange point"),
            ("is_jump_point", "jump point"),
            ("is_asteroid", "asteroid field"),
            ("is_man_made", "man-made"),
            ("is_planet", "planet orbit"),
            ("is_star", "star"),
        ) if o.get(key)),
        None,
    )
    if kind:
        parts.append(kind)
    parts.append("in-game" if o.get("is_available_live") else "not yet in-game")
    return " · ".join(parts)


async def _uex_poi(args: dict, ctx) -> str:
    q = (args.get("name") or "").strip()
    if not q:
        return "Give me a point-of-interest name."
    data, err = await _uex_get("poi")
    if err:
        return err
    p = _best_match(data, q, "name", "nickname")
    if not p:
        return f"No point of interest matching “{q}” on UEX."
    loc = (p.get("moon_name") or p.get("planet_name") or p.get("space_station_name")
           or p.get("city_name") or p.get("star_system_name"))
    parts = [f"**{p.get('name')}**"]
    if loc:
        parts.append(f"at {loc}")
    feats = [label for key, label in (
        ("has_trade_terminal", "trade terminal"), ("has_refuel", "refuel"),
        ("has_repair", "repair"), ("has_refinery", "refinery"),
        ("has_clinic", "clinic"), ("is_landable", "landable"),
    ) if p.get(key)]
    if feats:
        parts.append("has " + ", ".join(feats))
    return " · ".join(parts)


async def _uex_jump_point(args: dict, ctx) -> str:
    q = (args.get("system") or "").strip()
    data, err = await _uex_get("jump_points")
    if err:
        return err
    if not data:
        return "UEX lists no jump points."
    rows = data
    if q:
        nq = _norm(q)
        rows = [r for r in data
                if nq in _norm(r.get("star_system_origin_name"))
                or nq in _norm(r.get("star_system_destination_name"))]
        if not rows:
            return f"No jump points touching “{q}” on UEX."

    def _seg(r) -> str:
        o, d = r.get("star_system_origin_name") or "?", r.get("star_system_destination_name") or "?"
        if r.get("orbit_origin_name"):
            o += f" ({r['orbit_origin_name']})"
        if r.get("orbit_destination_name"):
            d += f" ({r['orbit_destination_name']})"
        return f"{o} ↔ {d}"

    lines = ["Jump points" + (f" touching {q}" if q else "") + ":"]
    lines += [f"- {_seg(r)}" for r in rows[:15]]
    return "\n".join(lines)


async def _uex_item(args: dict, ctx) -> str:
    name = (args.get("name") or "").strip()
    category = (args.get("category") or "").strip()
    cats, err = await _uex_get("categories")
    if err:
        return err
    item_cats = [c for c in (cats or []) if c.get("type") == "item" and c.get("name")]
    if not category:
        names = sorted({c["name"] for c in item_cats})
        return "Tell me which item category to search — e.g. " + ", ".join(names[:12]) + "."
    cat = _best_match(item_cats, category, "name")
    if not cat:
        return f"No item category matching “{category}” on UEX."
    rows, err = await _uex_get("items", {"id_category": cat.get("id")})
    if err:
        return err
    if not name:
        sample = ", ".join(r.get("name") for r in (rows or [])[:10] if r.get("name"))
        return f"**{cat.get('name')}** items on UEX: {sample}…" if sample else f"No items under {cat.get('name')}."
    it = _best_match(rows, name, "name", "slug")
    if not it:
        return f"No “{name}” item under {cat.get('name')} on UEX."
    parts = [f"**{it.get('name')}** ({cat.get('name')})"]
    if it.get("company_name"):
        parts.append(f"by {it['company_name']}")
    if it.get("size"):
        parts.append(f"size {it['size']}")
    return " · ".join(parts)


async def _uex_currency_index(args: dict, ctx) -> str:
    cur = (args.get("currency") or "UEC").strip().upper() or "UEC"
    data, err = await _uex_get("currencies_index", {"currency": cur})
    if err:
        return err
    rows = data if isinstance(data, list) else [data] if data else []
    row = next((r for r in rows if (r.get("currency") or "").upper() == cur), rows[0] if rows else None)
    if not row or row.get("index_value") is None:
        return f"No {cur} purchasing-power index on UEX right now."
    idx = float(row["index_value"])
    trend = "weaker (things cost more)" if idx > 100 else "stronger (things cost less)" if idx < 100 else "flat"
    return (f"**{cur} purchasing-power index: {idx:.1f}** "
            f"(100 = Dec 2023 baseline; {trend}).")


# ── on_enable: seed the Comm-Link page into the knowledge base ───────────────
async def _seed_kb(session: AsyncSession, guild_id: int) -> None:
    existing = await session.scalar(
        select(KBSource).where(
            KBSource.guild_id == guild_id, KBSource.uri == _COMM_LINK_URL
        )
    )
    if existing is not None:
        return  # idempotent — don't duplicate on re-enable
    session.add(
        KBSource(
            guild_id=guild_id,
            type=KBSourceType.url,
            uri=_COMM_LINK_URL,
            title="RSI Comm-Link",
            status=KBStatus.pending,
        )
    )
    log.info("Star Citizen extension enabled: queued Comm-Link KB source for guild %s", guild_id)


# ── Citizen profile scraper (used by the /citizen cog) ───────────────────────
async def fetch_citizen(handle: str) -> dict | None:
    """Scrape an RSI citizen profile into a dict, or None if not found/unreadable.
    Server-rendered HTML, so httpx + BeautifulSoup is enough (no JS engine)."""
    handle = (handle or "").strip().lstrip("@")
    if not handle:
        return None
    try:
        async with httpx.AsyncClient(headers=_UA, timeout=20.0, follow_redirects=True) as c:
            r = await c.get(_CITIZEN_BASE + quote(handle))
    except Exception:
        log.exception("citizen fetch failed for %s", handle)
        return None
    if r.status_code != 200:
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    profile = soup.select_one("#public-profile") or soup

    fields: dict[str, str] = {}
    for entry in profile.select("p.entry"):
        label = entry.select_one("span.label")
        value = entry.select_one(".value")
        if label and value:
            fields[label.get_text(strip=True).lower()] = value.get_text(strip=True)

    avatar_img = profile.select_one(".thumb img")
    avatar = urljoin(_RSI, avatar_img["src"]) if avatar_img and avatar_img.get("src") else None

    org: dict = {}
    org_block = soup.select_one(".main-org")
    if org_block:
        link = org_block.select_one("a.value")
        if link:
            org["name"] = link.get_text(strip=True)
            href = link.get("href") or ""
            if "/orgs/" in href:
                org["sid"] = href.rstrip("/").rsplit("/", 1)[-1]
        for entry in org_block.select("p.entry"):
            lbl, val = entry.select_one("span.label"), entry.select_one(".value")
            if lbl and val and "rank" in lbl.get_text(strip=True).lower():
                org["rank"] = val.get_text(strip=True)
        logo = org_block.select_one(".thumb img")
        if logo and logo.get("src"):
            org["logo"] = urljoin(_RSI, logo["src"])
        stars = org_block.select(".ranking .active")
        if stars:
            org["stars"] = len(stars)
    # Rank fallback from the <title> ("… | SID (Rank) - Roberts Space Industries").
    if org and "rank" not in org and soup.title:
        m = re.search(r"\(([^)]+)\)\s*-\s*Roberts Space Industries", soup.title.get_text())
        if m:
            org["rank"] = m.group(1)

    handle_name = fields.get("handle name")
    if not handle_name and not org:
        return None  # not a real profile (e.g. redirect/landing)

    return {
        "handle": handle_name or handle,
        "record": fields.get("uee citizen record"),
        "enlisted": fields.get("enlisted"),
        "fluency": fields.get("fluency"),
        "location": fields.get("location"),
        "bio": fields.get("bio"),
        "avatar": avatar,
        "org": org or None,
        "url": _CITIZEN_BASE + handle,
    }


# ── Registration ──────────────────────────────────────────────────────────────
def register_star_citizen() -> None:
    register(Extension(
        key="star_citizen",
        name="Star Citizen",
        description=(
            "Star Citizen toolkit: Executive Hangar timer, ship-matrix specs, live UEX "
            "trade/ship/location/economy data, /citizen profile lookup, and the RSI "
            "Comm-Link added to the knowledge base on enable."
        ),
        category="Games",
        default_enabled=False,
        on_enable=_seed_kb,
        system_note=(
            "Star Citizen tools are available (use them for SC questions and answer in "
            "your own words — no source tags): sc_hangar_status (Pyro Executive Hangar timer), "
            "sc_ship_lookup (ship specs). UEX reference: uex_commodity, uex_vehicle, "
            "uex_location, uex_star_system, uex_planet, uex_moon, uex_orbit, uex_poi, uex_jump_point, "
            "uex_item, uex_currency_index. UEX trading: uex_commodity_price (best buy/sell "
            "terminals), uex_commodity_route (profitable runs), uex_commodity_ranking (top "
            "earners), uex_commodity_status (stock labels), uex_vehicle_price (real-money "
            "pledge price), uex_vehicle_purchase (in-game aUEC buy location)."
        ),
        tools=(
            ExtensionTool(
                declaration=types.FunctionDeclaration(
                    name="sc_hangar_status",
                    description=(
                        "Current Star Citizen Pyro Executive Hangar status (open/closed) "
                        "and time until the next change. Use when asked about the exec "
                        "hangar / PYAM hangar timer."
                    ),
                    parameters=_obj({}, []),
                ),
                handler=_hangar_status,
            ),
            ExtensionTool(
                declaration=types.FunctionDeclaration(
                    name="sc_ship_lookup",
                    description=(
                        "Look up a Star Citizen ship's official specs from RSI's ship "
                        "matrix (manufacturer, role, size, crew, cargo, speed, status)."
                    ),
                    parameters=_obj({"name": _str("ship name, e.g. 'Aurora' or 'Constellation Andromeda'")}, ["name"]),
                ),
                handler=_ship_lookup,
            ),
            ExtensionTool(
                declaration=types.FunctionDeclaration(
                    name="uex_commodity",
                    description="Look up a Star Citizen commodity's UEX trade data (kind, avg buy/sell price, availability).",
                    parameters=_obj({"name": _str("commodity name or code, e.g. 'Quantanium' or 'AGRI'")}, ["name"]),
                ),
                handler=_uex_commodity,
            ),
            ExtensionTool(
                declaration=types.FunctionDeclaration(
                    name="uex_vehicle",
                    description="Look up a Star Citizen ship/vehicle in UEX's dataset (full name, cargo SCU, crew).",
                    parameters=_obj({"name": _str("vehicle name, e.g. 'Cutlass Black'")}, ["name"]),
                ),
                handler=_uex_vehicle,
            ),
            ExtensionTool(
                declaration=types.FunctionDeclaration(
                    name="uex_location",
                    description="Look up a Star Citizen trade terminal / station / location by name in UEX.",
                    parameters=_obj({"name": _str("location or terminal name, e.g. 'Area18' or 'CRU-L1'")}, ["name"]),
                ),
                handler=_uex_location,
            ),
            ExtensionTool(
                declaration=types.FunctionDeclaration(
                    name="uex_commodity_price",
                    description="Live per-terminal UEX prices for a commodity — the best place to buy and best place to sell it right now.",
                    parameters=_obj({"name": _str("commodity name or code, e.g. 'Quantanium'")}, ["name"]),
                ),
                handler=_uex_commodity_price,
            ),
            ExtensionTool(
                declaration=types.FunctionDeclaration(
                    name="uex_commodity_ranking",
                    description="UEX ranking of the most profitable Star Citizen commodities to trade (profit per SCU). No input needed.",
                    parameters=_obj({}, []),
                ),
                handler=_uex_commodity_ranking,
            ),
            ExtensionTool(
                declaration=types.FunctionDeclaration(
                    name="uex_commodity_route",
                    description=(
                        "Best profitable UEX trade routes. Give a commodity (best routes for it) "
                        "and/or an origin terminal/station (best runs starting there)."
                    ),
                    parameters=_obj({
                        "commodity": _str("optional commodity name, e.g. 'Laranite'"),
                        "origin": _str("optional origin terminal/station, e.g. 'Area18'"),
                    }, []),
                ),
                handler=_uex_commodity_route,
            ),
            ExtensionTool(
                declaration=types.FunctionDeclaration(
                    name="uex_commodity_status",
                    description="Explain the UEX terminal inventory/stock status levels (what the stock labels mean). No input needed.",
                    parameters=_obj({}, []),
                ),
                handler=_uex_commodity_status,
            ),
            ExtensionTool(
                declaration=types.FunctionDeclaration(
                    name="uex_vehicle_price",
                    description="A ship/vehicle's real-money pledge-store price (USD) from UEX — standalone and warbond, and whether it's on sale.",
                    parameters=_obj({"name": _str("ship name, e.g. 'Cutlass Black'")}, ["name"]),
                ),
                handler=_uex_vehicle_price,
            ),
            ExtensionTool(
                declaration=types.FunctionDeclaration(
                    name="uex_vehicle_purchase",
                    description="Where to buy a ship in-game for aUEC (cheapest terminal) per UEX. Distinct from the real-money pledge price.",
                    parameters=_obj({"name": _str("ship name, e.g. 'Avenger Titan'")}, ["name"]),
                ),
                handler=_uex_vehicle_purchase,
            ),
            ExtensionTool(
                declaration=types.FunctionDeclaration(
                    name="uex_star_system",
                    description="Star Citizen star-system info from UEX (faction, jurisdiction, playable yet). Omit the name to list playable systems.",
                    parameters=_obj({"name": _str("optional system name, e.g. 'Stanton' or 'Pyro'")}, []),
                ),
                handler=_uex_star_system,
            ),
            ExtensionTool(
                declaration=types.FunctionDeclaration(
                    name="uex_planet",
                    description="Star Citizen planet info from UEX (its star system, faction, whether it's in-game yet).",
                    parameters=_obj({"name": _str("planet name, e.g. 'Hurston' or 'microTech'")}, ["name"]),
                ),
                handler=_uex_planet,
            ),
            ExtensionTool(
                declaration=types.FunctionDeclaration(
                    name="uex_moon",
                    description="Star Citizen moon info from UEX (the planet it orbits and its star system).",
                    parameters=_obj({"name": _str("moon name, e.g. 'Cellin' or 'Daymar'")}, ["name"]),
                ),
                handler=_uex_moon,
            ),
            ExtensionTool(
                declaration=types.FunctionDeclaration(
                    name="uex_orbit",
                    description="Star Citizen orbital point from UEX — Lagrange points (e.g. CRU-L1), asteroid fields and other orbits: its star system and kind.",
                    parameters=_obj({"name": _str("orbit / Lagrange-point name, e.g. 'CRU-L1' or 'Yela'")}, ["name"]),
                ),
                handler=_uex_orbit,
            ),
            ExtensionTool(
                declaration=types.FunctionDeclaration(
                    name="uex_poi",
                    description="Star Citizen point-of-interest from UEX (its location and facilities — trade terminal, refuel, repair, refinery, etc).",
                    parameters=_obj({"name": _str("point-of-interest name, e.g. 'Jumptown' or 'Shubin Mining SAL-2'")}, ["name"]),
                ),
                handler=_uex_poi,
            ),
            ExtensionTool(
                declaration=types.FunctionDeclaration(
                    name="uex_jump_point",
                    description="Star Citizen jump points from UEX (which systems connect). Give a system to filter, or omit to list them all.",
                    parameters=_obj({"system": _str("optional star-system name to filter by, e.g. 'Stanton'")}, []),
                ),
                handler=_uex_jump_point,
            ),
            ExtensionTool(
                declaration=types.FunctionDeclaration(
                    name="uex_item",
                    description=(
                        "Look up a Star Citizen item (ship components, weapons, armor, etc.) in UEX. "
                        "A category is required — e.g. 'Coolers', 'Weapons', 'Armor'."
                    ),
                    parameters=_obj({
                        "name": _str("item name, e.g. 'Hydra Cooler'"),
                        "category": _str("item category, e.g. 'Coolers' or 'Weapons'"),
                    }, []),
                ),
                handler=_uex_item,
            ),
            ExtensionTool(
                declaration=types.FunctionDeclaration(
                    name="uex_currency_index",
                    description="The UEX aUEC purchasing-power index (100 = Dec 2023 baseline; higher = things cost more aUEC).",
                    parameters=_obj({"currency": _str("optional currency code, default 'UEC'")}, []),
                ),
                handler=_uex_currency_index,
            ),
        ),
    ))
