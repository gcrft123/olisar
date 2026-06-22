// Built-in SDK extension: Star Citizen toolkit. Ported from the original Python pack
// to the Olisar SDK — Executive Hangar timer, RSI ship matrix, live UEX trade/ship/
// location/economy lookups, the /citizen RSI profile command, and the Comm-Link seeded
// into the knowledge base on enable. Every network call degrades to a friendly string.
const UA = "OlisarBot/1.0 (Discord community bot; Star Citizen extension)";
const RSI = "https://robertsspaceindustries.com";
const CITIZEN_BASE = RSI + "/en/citizens/";
const COMM_LINK_URL = RSI + "/en/comm-link/";
const SHIP_MATRIX = RSI + "/ship-matrix/index";
const EXEC_URL = "https://exec.xyxyll.com/";
const EXEC_JS = "https://exec.xyxyll.com/app.js";
const UEX_BASE = "https://api.uexcorp.uk/2.0/";

// ── helpers ──────────────────────────────────────────────────────────────────
function commas(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ","); }
function auec(v) { var n = Math.round(Number(v)); return isFinite(n) ? commas(n) + " aUEC" : null; }
function norm(v) { return String(v == null ? "" : v).toLowerCase().replace(/[^a-z0-9]/g, ""); }

function lev(a, b) {
  var m = a.length, n = b.length;
  if (!m) return n; if (!n) return m;
  var prev = []; for (var j = 0; j <= n; j++) prev[j] = j;
  for (var i = 1; i <= m; i++) {
    var cur = [i];
    for (var k = 1; k <= n; k++) {
      var cost = a.charAt(i - 1) === b.charAt(k - 1) ? 0 : 1;
      cur[k] = Math.min(prev[k] + 1, cur[k - 1] + 1, prev[k - 1] + cost);
    }
    prev = cur;
  }
  return prev[n];
}

// Normalized-substring match on any key, then a typo-tolerant fuzzy fallback on the
// first key (so 'Quantanium' resolves to UEX's 'Quantainium').
function bestMatch(rows, query, keys) {
  var nq = norm(query);
  if (!nq || !rows) return null;
  var hits = rows.filter(function (r) { return keys.some(function (k) { return norm(r[k]).indexOf(nq) >= 0; }); });
  if (hits.length) {
    hits.sort(function (a, b) { return String(a[keys[0]] || "").length - String(b[keys[0]] || "").length; });
    return hits[0];
  }
  var ql = query.toLowerCase(), best = null, bestScore = 0;
  rows.forEach(function (r) {
    var name = String(r[keys[0]] || ""); if (!name) return;
    var nl = name.toLowerCase();
    var score = 1 - lev(ql, nl) / Math.max(ql.length, nl.length);
    if (score > bestScore) { bestScore = score; best = r; }
  });
  return bestScore >= 0.7 ? best : null;
}

async function uexGet(path, params) {
  var qs = "";
  if (params) {
    var pairs = Object.keys(params).filter(function (k) { return params[k] != null; })
      .map(function (k) { return encodeURIComponent(k) + "=" + encodeURIComponent(params[k]); });
    if (pairs.length) qs = "?" + pairs.join("&");
  }
  var headers = { "User-Agent": UA };
  var key = null;
  try { key = await host.secret("uex_api_key"); } catch (e) { /* key optional */ }
  if (key) headers["Authorization"] = "Bearer " + key;
  var r;
  try { r = await host.fetch(UEX_BASE + path + qs, { headers: headers }); }
  catch (e) { return [null, "Couldn't reach UEX right now."]; }
  if (r.status === 401) return [null, "That UEX endpoint needs a token — set the UEX API key in the dashboard."];
  var body;
  try { body = await r.json(); } catch (e) { return [null, "UEX returned an unexpected response."]; }
  if (body.status !== "ok") return [null, "UEX error: " + body.status];
  return [body.data || [], null];
}

async function resolveRow(path, query, keys) {
  var res = await uexGet(path); var data = res[0], err = res[1];
  if (err) return [null, err];
  return [bestMatch(data, query, keys), null];
}

// Commodity-trading terminals only (UEX type "commodity" or "commodity_raw"). Vending
// machines, shops, and fuel/refinery points never trade bulk cargo, so they must never
// resolve as a route origin — the old name-substring match picked "Hot Dogs - Seraphim
// Station" for an origin of "Seraphim Station" and produced a nonsense empty route.
var TERM_SELF_KEYS = ["name", "nickname", "code"];
var TERM_LOC_KEYS = ["space_station_name", "outpost_name", "city_name", "moon_name", "planet_name", "orbit_name", "star_system_name"];
var MAX_ROUTE_ORIGINS = 8;  // bound the per-location aggregation (and the host fetch budget)

function isCommodityTerminal(t) { return String(t.type || "").indexOf("commodity") >= 0; }

// Which parent-location name (if any) the query matched — for labelling a station/city/
// outpost-level origin, e.g. "Seraphim Station" → terminal "Admin - Seraphim".
function matchedLocation(t, nq) {
  for (var i = 0; i < TERM_LOC_KEYS.length; i++) {
    var v = t[TERM_LOC_KEYS[i]];
    if (v && norm(v).indexOf(nq) >= 0) return v;
  }
  return null;
}

// Resolve an origin string to the commodity terminals it refers to. A specific terminal
// name yields one; a station/city/outpost/planet yields ALL its commodity terminals so the
// route search can aggregate across them — the best run may leave from a side terminal, not
// one nominal "main" one. Returns { terminals, label, total, err }.
async function commodityTerminalsFor(query) {
  var res = await uexGet("terminals"); if (res[1]) return { err: res[1], terminals: [] };
  var comm = (res[0] || []).filter(isCommodityTerminal);
  if (!comm.length) comm = res[0] || [];  // schema without a usable type field — don't over-filter
  var nq = norm(query);
  if (!nq) return { terminals: [], label: query };
  // A terminal-name match is more specific than a location match, so it leads the list and
  // survives the cap; a location match brings in the rest of that location's terminals.
  var self = comm.filter(function (t) { return TERM_SELF_KEYS.some(function (k) { return norm(t[k]).indexOf(nq) >= 0; }); });
  var loc = comm.filter(function (t) { return matchedLocation(t, nq) != null; });
  var seen = {}, hits = [];
  self.concat(loc).forEach(function (t) { if (!seen[t.id]) { seen[t.id] = 1; hits.push(t); } });
  if (!hits.length) {  // typo-tolerant fallback, still commodity-only
    var fb = bestMatch(comm, query, ["name", "nickname", "code"]);
    if (fb) hits = [fb];
  }
  if (!hits.length) return { terminals: [], label: query };
  var label = hits.length === 1 ? hits[0].name : ((loc.length && matchedLocation(loc[0], nq)) || hits[0].name);
  return { terminals: hits.slice(0, MAX_ROUTE_ORIGINS), label: label, total: hits.length };
}

function routeParams(base, originId) {
  var p = {}; for (var k in base) p[k] = base[k];
  if (originId != null) p.id_terminal_origin = originId;
  return p;
}

function stripTags(s) {
  return String(s || "").replace(/<[^>]+>/g, "")
    .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&nbsp;/g, " ").trim();
}

// ── Executive Hangar status (replicates app.js cycle math) ────────────────────
async function hangarStatus() {
  var js, page;
  try {
    js = await (await host.fetch(EXEC_JS, { headers: { "User-Agent": UA } })).text();
    page = await (await host.fetch(EXEC_URL, { headers: { "User-Agent": UA } })).text();
  } catch (e) { return "Couldn't reach the Executive Hangar tracker right now."; }
  var state;
  try {
    var anchor = /INITIAL_OPEN_TIME\s*=\s*new Date\('([^']+)'\)/.exec(js)[1];
    var drift = parseInt(/CYCLE_DRIFT_MS\s*=\s*(\d+)/.exec(js)[1], 10);
    var onlineMin = parseInt(/DESIGN_ONLINE_MIN\s*=\s*(\d+)/.exec(js)[1], 10);
    var offlineMin = parseInt(/DESIGN_OFFLINE_MIN\s*=\s*(\d+)/.exec(js)[1], 10);
    var anchorMs = new Date(anchor).getTime();
    var onlineMs = onlineMin * 60000;
    var cycleMs = (onlineMin + offlineMin) * 60000 + drift;
    var openDur = Math.round(cycleMs * onlineMs / ((onlineMin + offlineMin) * 60000));
    var elapsed = Date.now() - anchorMs;
    var inCycle = ((elapsed % cycleMs) + cycleMs) % cycleMs;
    var status, remaining;
    if (inCycle < openDur) { status = "OPEN"; remaining = openDur - inCycle; }
    else { status = "CLOSED"; remaining = cycleMs - inCycle; }
    var mins = Math.floor(remaining / 60000), secs = Math.floor((remaining % 60000) / 1000);
    state = "The Pyro Executive Hangar is currently **" + status + "** — next change in ~" + mins + "m " + secs + "s.";
  } catch (e) {
    state = "Executive Hangar tracker reached, but I couldn't compute the live timer.";
  }
  var patch = /Patch\s+([0-9][^<\s]*)/.exec(page);
  if (patch) state += " (Star Citizen " + patch[1] + ")";
  return state;
}

// ── Ship matrix (RSI) ─────────────────────────────────────────────────────────
async function shipLookup(args) {
  var name = (args.name || "").trim();
  if (!name) return "Give me a ship name to look up.";
  var data;
  try { data = (await (await host.fetch(SHIP_MATRIX, { headers: { "User-Agent": UA } })).json()).data || []; }
  catch (e) { return "Couldn't reach the RSI ship matrix right now."; }
  var nl = name.toLowerCase();
  var matches = data.filter(function (s) { return (s.name || "").toLowerCase().indexOf(nl) >= 0; });
  if (!matches.length) return "No ship matching “" + name + "” in the RSI ship matrix.";
  matches.sort(function (a, b) { return (a.name || "").length - (b.name || "").length; });
  var s = matches[0];
  var man = (s.manufacturer && typeof s.manufacturer === "object") ? s.manufacturer : {};
  var header = "**" + s.name + "** — " + (man.name || "Unknown manufacturer");
  var bits = [];
  if (s.focus) bits.push("role: " + s.focus);
  if (s.size) bits.push("size: " + s.size);
  if (s.min_crew || s.max_crew) bits.push(s.min_crew !== s.max_crew ? "crew: " + s.min_crew + "–" + s.max_crew : "crew: " + s.max_crew);
  if (s.cargocapacity != null) bits.push("cargo: " + s.cargocapacity + " SCU");
  if (s.scm_speed) bits.push("SCM speed: " + s.scm_speed + " m/s");
  if (s.production_status) bits.push("status: " + s.production_status);
  var out = header + (bits.length ? "\n" + bits.join(" · ") : "");
  if (matches.length > 1) out += "\n(+" + (matches.length - 1) + " other name matches)";
  return out;
}

// ── UEX reference + trade tools ───────────────────────────────────────────────
async function uexCommodity(args) {
  var q = (args.name || "").trim(); if (!q) return "Give me a commodity name.";
  var res = await uexGet("commodities"); if (res[1]) return res[1];
  var c = bestMatch(res[0], q, ["name", "code"]);
  if (!c) return "No commodity matching “" + q + "” on UEX.";
  var parts = ["**" + c.name + "** (" + c.code + ") — " + (c.kind || "commodity")];
  if (c.price_buy) parts.push("avg buy " + commas(Math.round(c.price_buy)) + " aUEC");
  if (c.price_sell) parts.push("avg sell " + commas(Math.round(c.price_sell)) + " aUEC");
  parts.push(c.is_available ? "tradeable now" : "not currently traded");
  return parts.join(" · ");
}

async function uexVehicle(args) {
  var q = (args.name || "").trim(); if (!q) return "Give me a ship/vehicle name.";
  var res = await uexGet("vehicles"); if (res[1]) return res[1];
  var v = bestMatch(res[0], q, ["name_full", "name", "slug"]);
  if (!v) return "No vehicle matching “" + q + "” on UEX.";
  var parts = ["**" + (v.name_full || v.name) + "**"];
  if (v.scu != null) parts.push(v.scu + " SCU cargo");
  if (v.crew) parts.push("crew " + v.crew);
  if (v.is_concept) parts.push("concept (not flyable)");
  return parts.join(" · ");
}

var LOCATION_SOURCES = [["cities", "city"], ["space_stations", "station"], ["outposts", "outpost"], ["terminals", "terminal"]];
async function uexLocation(args) {
  var q = (args.name || "").trim(); if (!q) return "Give me a station/terminal/location name.";
  var lastErr = null;
  for (var i = 0; i < LOCATION_SOURCES.length; i++) {
    var res = await uexGet(LOCATION_SOURCES[i][0]);
    if (res[1]) { lastErr = res[1]; continue; }
    var loc = bestMatch(res[0], q, ["name", "nickname", "code"]);
    if (loc) {
      var out = "**" + loc.name + "** (" + (loc.type || LOCATION_SOURCES[i][1]) + ")";
      if (loc.nickname && loc.nickname !== loc.name) out += " — " + loc.nickname;
      return out;
    }
  }
  return lastErr || "No location matching “" + q + "” on UEX.";
}

async function uexCommodityPrice(args) {
  var q = (args.name || "").trim(); if (!q) return "Give me a commodity name.";
  var cr = await resolveRow("commodities", q, ["name", "code"]); if (cr[1]) return cr[1];
  var c = cr[0]; if (!c) return "No commodity matching “" + q + "” on UEX.";
  var res = await uexGet("commodities_prices", { id_commodity: c.id }); if (res[1]) return res[1];
  var rows = res[0]; if (!rows || !rows.length) return "No live terminal prices for **" + c.name + "** on UEX right now.";
  var sells = rows.filter(function (r) { return (r.price_sell || 0) > 0; });
  var buys = rows.filter(function (r) { return (r.price_buy || 0) > 0; });
  var parts = ["**" + c.name + "** live prices (" + rows.length + " terminal(s))"];
  if (sells.length) { var bs = sells.reduce(function (a, b) { return b.price_sell > a.price_sell ? b : a; }); parts.push("best sell " + auec(bs.price_sell) + " at " + (bs.terminal_name || "?")); }
  if (buys.length) { var bb = buys.reduce(function (a, b) { return b.price_buy < a.price_buy ? b : a; }); parts.push("best buy " + auec(bb.price_buy) + " at " + (bb.terminal_name || "?")); }
  if (!sells.length && !buys.length) parts.push("listed, but no active buy/sell prices");
  return parts.join(" · ");
}

async function uexCommodityRanking() {
  var res = await uexGet("commodities_ranking"); if (res[1]) return res[1];
  var rows = (res[0] || []).filter(function (r) { return r.name; });
  rows.sort(function (a, b) { return (b.profitability || 0) - (a.profitability || 0); });
  if (rows.length) {
    var lines = ["Top UEX commodities by profit per SCU:"];
    rows.slice(0, 5).forEach(function (r, i) { lines.push((i + 1) + ". **" + r.name + "** — " + (auec(r.profitability) || "?") + "/SCU"); });
    return lines.join("\n");
  }
  var cres = await uexGet("commodities"); if (cres[1]) return cres[1];
  var ranked = (cres[0] || []).filter(function (c) { return c.name && (c.price_buy || 0) > 0 && (c.price_sell || 0) > (c.price_buy || 0); })
    .map(function (c) { return { margin: c.price_sell - c.price_buy, name: c.name }; })
    .sort(function (a, b) { return b.margin - a.margin; });
  if (!ranked.length) return "UEX returned no commodity ranking.";
  var out = ["Top UEX commodities by average buy→sell margin:"];
  ranked.slice(0, 5).forEach(function (r, i) { out.push((i + 1) + ". **" + r.name + "** — ~" + auec(r.margin) + "/SCU"); });
  return out.join("\n");
}

async function uexCommodityRoute(args) {
  var commodity = (args.commodity || "").trim(), origin = (args.origin || "").trim();
  if (!commodity && !origin) return "Give me a commodity and/or an origin terminal to find trade routes.";
  var base = {}, label = [];
  if (commodity) {
    var cr = await resolveRow("commodities", commodity, ["name", "code"]); if (cr[1]) return cr[1];
    if (!cr[0]) return "No commodity matching “" + commodity + "” on UEX.";
    base.id_commodity = cr[0].id; label.push(cr[0].name);
  }
  var origins = null, originTotal = 0;
  if (origin) {
    var ot = await commodityTerminalsFor(origin); if (ot.err) return ot.err;
    if (!ot.terminals.length) {
      return "No commodity-trading terminal matching “" + origin + "” on UEX — vending machines, " +
        "shops and fuel/refinery points don't trade cargo. Try a station's Admin/TDD terminal, a city, or a mining outpost.";
    }
    origins = ot.terminals; originTotal = ot.total;
    label.push("from " + ot.label);
  }
  var rows;
  if (origins) {
    // Aggregate routes across every commodity terminal at the origin location — the best run
    // from a station may leave from a side terminal, not one nominal "main" one. Dedupe by
    // commodity + origin + destination so overlapping terminal queries don't double-list.
    var seen = {}, agg = [];
    for (var i = 0; i < origins.length; i++) {
      var rr = await uexGet("commodities_routes", routeParams(base, origins[i].id));
      if (rr[1]) continue;  // skip a terminal UEX can't price; keep aggregating the rest
      (rr[0] || []).forEach(function (r) {
        var k = (r.commodity_name || "") + "|" + (r.origin_terminal_name || "") + "|" + (r.destination_terminal_name || "");
        if (!seen[k]) { seen[k] = 1; agg.push(r); }
      });
    }
    rows = agg;
  } else {
    var res = await uexGet("commodities_routes", base); if (res[1]) return res[1];
    rows = res[0];
  }
  if (!rows || !rows.length) return "No profitable routes for " + label.join(" ") + " on UEX right now.";
  rows.sort(function (a, b) { return (b.profit || 0) - (a.profit || 0); });
  var header = "Top trade routes (" + label.join(" ") + ")";
  if (origins && originTotal > origins.length) header += " — sampled " + origins.length + " of " + originTotal + " terminals";
  var lines = [header + ":"];
  rows.slice(0, 3).forEach(function (r, i) {
    var bits = [(r.origin_terminal_name || "?") + " → " + (r.destination_terminal_name || "?")];
    if (r.commodity_name && !commodity) bits.push(r.commodity_name);
    if (r.price_margin) bits.push(auec(r.price_margin) + "/SCU");
    if (r.profit) bits.push(auec(r.profit) + " total");
    if (r.scu_margin) bits.push(Math.round(r.scu_margin) + " SCU");
    lines.push((i + 1) + ". " + bits.join(" · "));
  });
  return lines.join("\n");
}

async function uexCommodityStatus() {
  var res = await uexGet("commodities_status"); if (res[1]) return res[1];
  var data = res[0];
  var rows = (data && !Array.isArray(data)) ? (data.sell || data.buy) : data;
  rows = rows || [];
  if (!rows.length) return "UEX returned no inventory status levels.";
  rows.sort(function (a, b) { return (a.percentage_start || 0) - (b.percentage_start || 0); });
  var lines = ["UEX terminal inventory levels (what the stock labels mean):"];
  rows.forEach(function (r) {
    var name = r.name || r.name_short || r.code;
    lines.push("- **" + name + "**" + (r.percentage ? " — " + r.percentage : ""));
  });
  return lines.join("\n");
}

async function uexVehiclePrice(args) {
  var q = (args.name || "").trim(); if (!q) return "Give me a ship/vehicle name.";
  var vr = await resolveRow("vehicles", q, ["name_full", "name", "slug"]); if (vr[1]) return vr[1];
  var v = vr[0]; if (!v) return "No vehicle matching “" + q + "” on UEX.";
  var name = v.name_full || v.name;
  var res = await uexGet("vehicles_prices", { id_vehicle: v.id }); if (res[1]) return res[1];
  var rows = res[0]; if (!rows || !rows.length) return "No pledge-store price listed for **" + name + "** on UEX.";
  var r = rows[0], cur = r.currency || "USD";
  var parts = ["**" + name + "** — pledge store"];
  if (r.price) parts.push(r.price + " " + cur + " standalone");
  if (r.price_warbond) parts.push(r.price_warbond + " " + cur + " warbond");
  if (r.on_sale) parts.push("on sale now");
  return parts.join(" · ") + " (real money)";
}

async function uexVehiclePurchase(args) {
  var q = (args.name || "").trim(); if (!q) return "Give me a ship/vehicle name.";
  var vr = await resolveRow("vehicles", q, ["name_full", "name", "slug"]); if (vr[1]) return vr[1];
  var v = vr[0]; if (!v) return "No vehicle matching “" + q + "” on UEX.";
  var name = v.name_full || v.name;
  var res = await uexGet("vehicles_purchases_prices", { id_vehicle: v.id }); if (res[1]) return res[1];
  var buys = (res[0] || []).filter(function (r) { return (r.price_buy || 0) > 0; });
  if (!buys.length) return "No in-game (aUEC) purchase location for **" + name + "** on UEX — it may be pledge-only.";
  buys.sort(function (a, b) { return a.price_buy - b.price_buy; });
  var best = buys[0];
  var where = best.terminal_name || best.city_name || best.outpost_name || "?";
  var out = "**" + name + "** — cheapest in-game buy " + auec(best.price_buy) + " at " + where;
  if (buys.length > 1) out += " (+" + (buys.length - 1) + " other location" + (buys.length > 2 ? "s" : "") + ")";
  return out;
}

async function uexStarSystem(args) {
  var q = (args.name || "").trim();
  var res = await uexGet("star_systems"); if (res[1]) return res[1];
  var data = res[0]; if (!data || !data.length) return "UEX lists no star systems.";
  if (!q) {
    var live = data.filter(function (s) { return s.is_available_live && s.name; }).map(function (s) { return s.name; }).sort();
    return live.length ? "Star systems playable now: " + live.join(", ") : "No live star systems.";
  }
  var s = bestMatch(data, q, ["name", "code"]);
  if (!s) return "No star system matching “" + q + "” on UEX.";
  var parts = ["**" + s.name + "** system"];
  if (s.faction_name) parts.push("controlled by " + s.faction_name);
  if (s.jurisdiction_name) parts.push(s.jurisdiction_name);
  parts.push(s.is_available_live ? "playable now" : "not yet in-game");
  return parts.join(" · ");
}

async function uexPlanet(args) {
  var q = (args.name || "").trim(); if (!q) return "Give me a planet name.";
  var res = await uexGet("planets"); if (res[1]) return res[1];
  var p = bestMatch(res[0], q, ["name", "name_origin", "code"]);
  if (!p) return "No planet matching “" + q + "” on UEX.";
  var parts = ["**" + p.name + "**"];
  if (p.star_system_name) parts.push("in " + p.star_system_name);
  if (p.faction_name) parts.push("faction " + p.faction_name);
  parts.push(p.is_available_live ? "in-game" : "not yet in-game");
  return parts.join(" · ");
}

async function uexMoon(args) {
  var q = (args.name || "").trim(); if (!q) return "Give me a moon name.";
  var res = await uexGet("moons"); if (res[1]) return res[1];
  var m = bestMatch(res[0], q, ["name", "name_origin", "code"]);
  if (!m) return "No moon matching “" + q + "” on UEX.";
  var parts = ["**" + m.name + "**"];
  if (m.planet_name) parts.push("orbiting " + m.planet_name);
  if (m.star_system_name) parts.push("in " + m.star_system_name);
  parts.push(m.is_available_live ? "in-game" : "not yet in-game");
  return parts.join(" · ");
}

var ORBIT_KINDS = [["is_lagrange", "Lagrange point"], ["is_jump_point", "jump point"], ["is_asteroid", "asteroid field"], ["is_man_made", "man-made"], ["is_planet", "planet orbit"], ["is_star", "star"]];
async function uexOrbit(args) {
  var q = (args.name || "").trim(); if (!q) return "Give me an orbit or Lagrange-point name.";
  var res = await uexGet("orbits"); if (res[1]) return res[1];
  var o = bestMatch(res[0], q, ["name", "name_origin", "code"]);
  if (!o) return "No orbit matching “" + q + "” on UEX.";
  var parts = ["**" + o.name + "**"];
  if (o.star_system_name) parts.push("in " + o.star_system_name);
  for (var i = 0; i < ORBIT_KINDS.length; i++) { if (o[ORBIT_KINDS[i][0]]) { parts.push(ORBIT_KINDS[i][1]); break; } }
  parts.push(o.is_available_live ? "in-game" : "not yet in-game");
  return parts.join(" · ");
}

var POI_FEATS = [["has_trade_terminal", "trade terminal"], ["has_refuel", "refuel"], ["has_repair", "repair"], ["has_refinery", "refinery"], ["has_clinic", "clinic"], ["is_landable", "landable"]];
async function uexPoi(args) {
  var q = (args.name || "").trim(); if (!q) return "Give me a point-of-interest name.";
  var res = await uexGet("poi"); if (res[1]) return res[1];
  var p = bestMatch(res[0], q, ["name", "nickname"]);
  if (!p) return "No point of interest matching “" + q + "” on UEX.";
  var loc = p.moon_name || p.planet_name || p.space_station_name || p.city_name || p.star_system_name;
  var parts = ["**" + p.name + "**"];
  if (loc) parts.push("at " + loc);
  var feats = POI_FEATS.filter(function (f) { return p[f[0]]; }).map(function (f) { return f[1]; });
  if (feats.length) parts.push("has " + feats.join(", "));
  return parts.join(" · ");
}

async function uexJumpPoint(args) {
  var q = (args.system || "").trim();
  var res = await uexGet("jump_points"); if (res[1]) return res[1];
  var data = res[0]; if (!data || !data.length) return "UEX lists no jump points.";
  var rows = data;
  if (q) {
    var nq = norm(q);
    rows = data.filter(function (r) { return norm(r.star_system_origin_name).indexOf(nq) >= 0 || norm(r.star_system_destination_name).indexOf(nq) >= 0; });
    if (!rows.length) return "No jump points touching “" + q + "” on UEX.";
  }
  function seg(r) {
    var o = r.star_system_origin_name || "?", d = r.star_system_destination_name || "?";
    if (r.orbit_origin_name) o += " (" + r.orbit_origin_name + ")";
    if (r.orbit_destination_name) d += " (" + r.orbit_destination_name + ")";
    return o + " ↔ " + d;
  }
  var lines = ["Jump points" + (q ? " touching " + q : "") + ":"];
  rows.slice(0, 15).forEach(function (r) { lines.push("- " + seg(r)); });
  return lines.join("\n");
}

async function uexItem(args) {
  var name = (args.name || "").trim(), category = (args.category || "").trim();
  var cres = await uexGet("categories"); if (cres[1]) return cres[1];
  var itemCats = (cres[0] || []).filter(function (c) { return c.type === "item" && c.name; });
  if (!category) {
    var names = itemCats.map(function (c) { return c.name; }).sort();
    return "Tell me which item category to search — e.g. " + names.slice(0, 12).join(", ") + ".";
  }
  var cat = bestMatch(itemCats, category, ["name"]);
  if (!cat) return "No item category matching “" + category + "” on UEX.";
  var res = await uexGet("items", { id_category: cat.id }); if (res[1]) return res[1];
  var rows = res[0];
  if (!name) {
    var sample = (rows || []).slice(0, 10).map(function (r) { return r.name; }).filter(Boolean).join(", ");
    return sample ? "**" + cat.name + "** items on UEX: " + sample + "…" : "No items under " + cat.name + ".";
  }
  var it = bestMatch(rows, name, ["name", "slug"]);
  if (!it) return "No “" + name + "” item under " + cat.name + " on UEX.";
  var parts = ["**" + it.name + "** (" + cat.name + ")"];
  if (it.company_name) parts.push("by " + it.company_name);
  if (it.size) parts.push("size " + it.size);
  return parts.join(" · ");
}

async function uexCurrencyIndex(args) {
  var cur = ((args.currency || "UEC").trim().toUpperCase()) || "UEC";
  var res = await uexGet("currencies_index", { currency: cur }); if (res[1]) return res[1];
  var data = res[0];
  var rows = Array.isArray(data) ? data : (data ? [data] : []);
  var row = rows.filter(function (r) { return (r.currency || "").toUpperCase() === cur; })[0] || rows[0];
  if (!row || row.index_value == null) return "No " + cur + " purchasing-power index on UEX right now.";
  var idx = Number(row.index_value);
  var trend = idx > 100 ? "weaker (things cost more)" : idx < 100 ? "stronger (things cost less)" : "flat";
  return "**" + cur + " purchasing-power index: " + idx.toFixed(1) + "** (100 = Dec 2023 baseline; " + trend + ").";
}

// ── /citizen RSI profile scraper ──────────────────────────────────────────────
function matchField(html, label) {
  var re = new RegExp('<span class="label">\\s*' + label + '[\\s\\S]*?class="value"[^>]*>([\\s\\S]*?)<\\/', 'i');
  var m = re.exec(html);
  return m ? stripTags(m[1]) : null;
}

async function fetchCitizen(handle) {
  handle = (handle || "").trim().replace(/^@/, "");
  if (!handle) return null;
  var r;
  try { r = await host.fetch(CITIZEN_BASE + encodeURIComponent(handle), { headers: { "User-Agent": UA } }); }
  catch (e) { return null; }
  if (r.status !== 200) return null;
  var html = await r.text();
  var handleName = matchField(html, "Handle name");
  var orgName = null, orgRank = null;
  var orgBlock = /<div[^>]*class="[^"]*main-org[^"]*"[\s\S]*?<\/div>\s*<\/div>/i.exec(html);
  var scope = orgBlock ? orgBlock[0] : "";
  var orgLink = /class="value"[^>]*href="([^"]*\/orgs\/[^"]*)"[^>]*>([\s\S]*?)<\//i.exec(scope) ||
                /href="([^"]*\/orgs\/[^"]*)"[^>]*class="value"[^>]*>([\s\S]*?)<\//i.exec(scope);
  if (orgLink) orgName = stripTags(orgLink[2]);
  if (scope) orgRank = matchField(scope, "Organization rank") || matchField(scope, "rank");
  // Rank fallback from <title> "… (Rank) - Roberts Space Industries".
  if (orgName && !orgRank) {
    var t = /<title>([\s\S]*?)<\/title>/i.exec(html);
    if (t) { var tm = /\(([^)]+)\)\s*-\s*Roberts Space Industries/.exec(t[1]); if (tm) orgRank = tm[1]; }
  }
  var avatar = null;
  var av = /class="thumb"[^>]*>\s*<img[^>]*src="([^"]+)"/i.exec(html);
  if (av) avatar = av[1].indexOf("http") === 0 ? av[1] : RSI + av[1];
  if (!handleName && !orgName) return null;
  return {
    handle: handleName || handle,
    record: matchField(html, "UEE Citizen Record"),
    enlisted: matchField(html, "Enlisted"),
    fluency: matchField(html, "Fluency"),
    location: matchField(html, "Location"),
    avatar: avatar,
    org: orgName ? { name: orgName, rank: orgRank } : null,
    url: CITIZEN_BASE + handle,
  };
}

async function citizenCommand(i) {
  var handle = i.options.username;
  var data = await fetchCitizen(handle);
  if (!data) { await i.reply("I couldn't find a citizen named **" + handle + "** on RSI."); return; }
  var fields = [];
  if (data.record) fields.push({ name: "Citizen record", value: data.record, inline: true });
  if (data.enlisted) fields.push({ name: "Enlisted", value: data.enlisted, inline: true });
  if (data.location) fields.push({ name: "Location", value: data.location, inline: true });
  if (data.fluency) fields.push({ name: "Fluency", value: data.fluency, inline: true });
  if (data.org) fields.push({ name: "Main org", value: data.org.name + (data.org.rank ? " — " + data.org.rank : ""), inline: false });
  var embed = host.embed({
    title: data.handle, url: data.url, color: 0x2e9fff,
    thumbnail: data.avatar || undefined, fields: fields,
    footer: "Roberts Space Industries",
  });
  await i.reply({ embed: embed });
}

// ── definition ────────────────────────────────────────────────────────────────
defineExtension({
  id: "star_citizen",
  name: "Star Citizen",
  version: "1.1.0",
  category: "Games",
  description:
    "Star Citizen toolkit: Executive Hangar timer, ship-matrix specs, live UEX " +
    "trade/ship/location/economy data, /citizen profile lookup, and the RSI " +
    "Comm-Link added to the knowledge base on enable.",
  defaultEnabled: false,
  permissions: ["fetch", "secret:uex_api_key", "discord.reply"],
  systemNote:
    "Star Citizen tools are available (use them for SC questions and answer in your own " +
    "words — no source tags): sc_hangar_status (Pyro Executive Hangar timer), sc_ship_lookup " +
    "(ship specs). UEX reference: uex_commodity, uex_vehicle, uex_location, uex_star_system, " +
    "uex_planet, uex_moon, uex_orbit, uex_poi, uex_jump_point, uex_item, uex_currency_index. " +
    "UEX trading: uex_commodity_price (best buy/sell terminals), uex_commodity_route " +
    "(profitable runs), uex_commodity_ranking (top earners), uex_commodity_status (stock " +
    "labels), uex_vehicle_price (real-money pledge price), uex_vehicle_purchase (in-game aUEC buy).",
  seeds: { kbSources: [{ type: "url", uri: COMM_LINK_URL, title: "RSI Comm-Link" }] },
  commands: [{
    name: "citizen",
    description: "Look up a Star Citizen player's RSI profile.",
    options: [{ name: "username", type: "string", description: "The player's RSI handle, e.g. DadBodNerd", required: true }],
    handler: citizenCommand,
  }],
  tools: [
    { name: "sc_hangar_status", description: "Current Star Citizen Pyro Executive Hangar status (open/closed) and time until the next change. Use when asked about the exec hangar / PYAM hangar timer.", parameters: { type: "object", properties: {} }, handler: hangarStatus },
    { name: "sc_ship_lookup", description: "Look up a Star Citizen ship's official specs from RSI's ship matrix (manufacturer, role, size, crew, cargo, speed, status).", parameters: { type: "object", properties: { name: { type: "string", description: "ship name, e.g. 'Aurora' or 'Constellation Andromeda'" } }, required: ["name"] }, handler: shipLookup },
    { name: "uex_commodity", description: "Look up a Star Citizen commodity's UEX trade data (kind, avg buy/sell price, availability).", parameters: { type: "object", properties: { name: { type: "string", description: "commodity name or code, e.g. 'Quantanium' or 'AGRI'" } }, required: ["name"] }, handler: uexCommodity },
    { name: "uex_vehicle", description: "Look up a Star Citizen ship/vehicle in UEX's dataset (full name, cargo SCU, crew).", parameters: { type: "object", properties: { name: { type: "string", description: "vehicle name, e.g. 'Cutlass Black'" } }, required: ["name"] }, handler: uexVehicle },
    { name: "uex_location", description: "Look up a Star Citizen trade terminal / station / location by name in UEX.", parameters: { type: "object", properties: { name: { type: "string", description: "location or terminal name, e.g. 'Area18' or 'CRU-L1'" } }, required: ["name"] }, handler: uexLocation },
    { name: "uex_commodity_price", description: "Live per-terminal UEX prices for a commodity — the best place to buy and best place to sell it right now.", parameters: { type: "object", properties: { name: { type: "string", description: "commodity name or code, e.g. 'Quantanium'" } }, required: ["name"] }, handler: uexCommodityPrice },
    { name: "uex_commodity_ranking", description: "UEX ranking of the most profitable Star Citizen commodities to trade (profit per SCU). No input needed.", parameters: { type: "object", properties: {} }, handler: uexCommodityRanking },
    { name: "uex_commodity_route", description: "Best profitable UEX trade routes. Give a commodity (best routes for it) and/or an origin (best runs starting there). The origin can be a specific terminal OR a whole station/city/outpost — it aggregates across that location's commodity terminals.", parameters: { type: "object", properties: { commodity: { type: "string", description: "optional commodity name, e.g. 'Laranite'" }, origin: { type: "string", description: "optional origin: a terminal, station, city or outpost, e.g. 'Area18' or 'Seraphim Station'" } } }, handler: uexCommodityRoute },
    { name: "uex_commodity_status", description: "Explain the UEX terminal inventory/stock status levels (what the stock labels mean). No input needed.", parameters: { type: "object", properties: {} }, handler: uexCommodityStatus },
    { name: "uex_vehicle_price", description: "A ship/vehicle's real-money pledge-store price (USD) from UEX — standalone and warbond, and whether it's on sale.", parameters: { type: "object", properties: { name: { type: "string", description: "ship name, e.g. 'Cutlass Black'" } }, required: ["name"] }, handler: uexVehiclePrice },
    { name: "uex_vehicle_purchase", description: "Where to buy a ship in-game for aUEC (cheapest terminal) per UEX. Distinct from the real-money pledge price.", parameters: { type: "object", properties: { name: { type: "string", description: "ship name, e.g. 'Avenger Titan'" } }, required: ["name"] }, handler: uexVehiclePurchase },
    { name: "uex_star_system", description: "Star Citizen star-system info from UEX (faction, jurisdiction, playable yet). Omit the name to list playable systems.", parameters: { type: "object", properties: { name: { type: "string", description: "optional system name, e.g. 'Stanton' or 'Pyro'" } } }, handler: uexStarSystem },
    { name: "uex_planet", description: "Star Citizen planet info from UEX (its star system, faction, whether it's in-game yet).", parameters: { type: "object", properties: { name: { type: "string", description: "planet name, e.g. 'Hurston' or 'microTech'" } }, required: ["name"] }, handler: uexPlanet },
    { name: "uex_moon", description: "Star Citizen moon info from UEX (the planet it orbits and its star system).", parameters: { type: "object", properties: { name: { type: "string", description: "moon name, e.g. 'Cellin' or 'Daymar'" } }, required: ["name"] }, handler: uexMoon },
    { name: "uex_orbit", description: "Star Citizen orbital point from UEX — Lagrange points (e.g. CRU-L1), asteroid fields and other orbits: its star system and kind.", parameters: { type: "object", properties: { name: { type: "string", description: "orbit / Lagrange-point name, e.g. 'CRU-L1' or 'Yela'" } }, required: ["name"] }, handler: uexOrbit },
    { name: "uex_poi", description: "Star Citizen point-of-interest from UEX (its location and facilities — trade terminal, refuel, repair, refinery, etc).", parameters: { type: "object", properties: { name: { type: "string", description: "point-of-interest name, e.g. 'Jumptown' or 'Shubin Mining SAL-2'" } }, required: ["name"] }, handler: uexPoi },
    { name: "uex_jump_point", description: "Star Citizen jump points from UEX (which systems connect). Give a system to filter, or omit to list them all.", parameters: { type: "object", properties: { system: { type: "string", description: "optional star-system name to filter by, e.g. 'Stanton'" } } }, handler: uexJumpPoint },
    { name: "uex_item", description: "Look up a Star Citizen item (ship components, weapons, armor, etc.) in UEX. A category is required — e.g. 'Coolers', 'Weapons', 'Armor'.", parameters: { type: "object", properties: { name: { type: "string", description: "item name, e.g. 'Hydra Cooler'" }, category: { type: "string", description: "item category, e.g. 'Coolers' or 'Weapons'" } } }, handler: uexItem },
    { name: "uex_currency_index", description: "The UEX aUEC purchasing-power index (100 = Dec 2023 baseline; higher = things cost more aUEC).", parameters: { type: "object", properties: { currency: { type: "string", description: "optional currency code, default 'UEC'" } } }, handler: uexCurrencyIndex },
  ],
});
