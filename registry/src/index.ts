/**
 * Olisar extension marketplace registry.
 *
 * A Cloudflare Worker that hosts the catalog (D1) and the `.olx` bundle blobs (R2).
 * This phase is the read-only **consume** API the bot's console browses + installs from;
 * the bot always re-transpiles and verifies the bundle locally, so the registry is a
 * discovery + distribution layer, never a trusted compiler.
 *
 * Routes (the main ones; see the router below for the rest):
 *   GET  /v1/health
 *   GET  /v1/search?q=&category=&limit=&offset=
 *   GET  /v1/ext/:namespace/:name              → catalog detail + versions
 *   GET  /v1/ext/:namespace/:name/:version     → the .olx bundle (JSON, from R2)
 *   POST /v1/publishers/challenge               → single-use nonce for register
 *   POST /v1/publishers/register                → claim a handle / rotate the token (signed nonce)
 *   POST /v1/publishers/verify                  → bind a Discord id via Discord OAuth
 *   POST /v1/_dev/publish                       → seed (local only; gated by DEV_SEED)
 */

export interface Env {
  DB: D1Database;
  BUNDLES: R2Bucket;
  DEV_SEED?: string;       // local seeding endpoint (set in .dev.vars only)
  ADMIN_TOKEN?: string;    // bearer token gating /v1/admin/publish (a Worker secret)
  R2_MAX_BYTES?: string;   // hard storage cap (default 9 GB, under the 10 GB free tier)
  R2_MAX_BUNDLE_BYTES?: string; // per-bundle cap (default 1 MB)
  R2_CLASS_A_MAX?: string; // monthly R2 write cap (default 900k, under 1M free)
  R2_REPORT_MAX_BYTES?: string;   // share of storage report attachments may use (default 500 MB)
  R2_REPORT_CLASS_A_MAX?: string; // share of monthly writes reports may use (default 100k)
  PUBLISHER_MAX_BYTES?: string;       // storage per publisher (default 100 MB)
  PUBLISHER_DAILY_PUBLISHES?: string; // new versions per publisher per UTC day (default 30)
  RESEND_API_KEY?: string; // Resend API key for abuse-report emails (a Worker secret)
  REPORT_EMAIL?: string;   // where abuse reports are emailed (the platform owner)
  REPORT_FROM?: string;    // From address (default "Olisar <onboarding@resend.dev>")
}

const CORS = { "access-control-allow-origin": "*" };

// Free-tier guardrails. R2 egress is free and reads (Class B, 10M/mo) stay under the
// limit via the Workers free-plan request cap (~100k/day); the unbounded risks are
// storage and writes (Class A), which we cap exactly below.
function capInt(v: string | undefined, dflt: number): number {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : dflt;
}
function limits(env: Env) {
  return {
    maxBytes: capInt(env.R2_MAX_BYTES, 9_000_000_000),
    maxBundle: capInt(env.R2_MAX_BUNDLE_BYTES, 1_000_000),
    maxClassA: capInt(env.R2_CLASS_A_MAX, 900_000),
  };
}

// ── quotas ──────────────────────────────────────────────────────────────────
// Every R2 write reserves its bytes and one write against one or more counters first. A
// counter row holds bytes stored so far and writes in the current period; `reserve` checks
// both caps and adds to them in a single statement, so two requests can't each pass the
// check and overshoot together, and neither overwrites the other's count.
//
// Three kinds of counter: the whole bucket (usage id 1, the free-tier guard), the slice
// abuse-report attachments may use (usage id 2, so a flood of reports can't use up what
// publishing needs), and one per publisher (publisher_usage), so one publisher can't use up
// everyone else's share either.
interface Quota {
  table: "usage" | "publisher_usage";
  keyCol: "id" | "publisher_id";
  key: number;
  writesCol: "class_a" | "publishes";
  period: string;   // the writes count resets when this changes
  maxBytes: number;
  maxWrites: number;
  label: string;    // names the cap in the error when it's reached
}

function monthPeriod(): string {
  return new Date().toISOString().slice(0, 7); // YYYY-MM
}

function globalQuota(env: Env): Quota {
  const lim = limits(env);
  return {
    table: "usage", keyCol: "id", key: 1, writesCol: "class_a", period: monthPeriod(),
    maxBytes: lim.maxBytes, maxWrites: lim.maxClassA, label: "registry",
  };
}

function reportQuota(env: Env): Quota {
  return {
    table: "usage", keyCol: "id", key: 2, writesCol: "class_a", period: monthPeriod(),
    maxBytes: capInt(env.R2_REPORT_MAX_BYTES, 500_000_000),
    maxWrites: capInt(env.R2_REPORT_CLASS_A_MAX, 100_000), label: "report storage",
  };
}

function publisherQuota(env: Env, publisherId: number): Quota {
  return {
    table: "publisher_usage", keyCol: "publisher_id", key: publisherId, writesCol: "publishes",
    period: new Date().toISOString().slice(0, 10), // YYYY-MM-DD (UTC)
    maxBytes: capInt(env.PUBLISHER_MAX_BYTES, 100_000_000),
    maxWrites: capInt(env.PUBLISHER_DAILY_PUBLISHES, 30), label: "publisher",
  };
}

// Returns the counter's new byte total, or null when either cap would be exceeded (and then
// nothing was added). The insert branch only runs for a counter's first write.
async function reserve(env: Env, q: Quota, bytes: number): Promise<number | null> {
  if (bytes > q.maxBytes || q.maxWrites < 1) return null;
  const row = await env.DB.prepare(
    `INSERT INTO ${q.table} (${q.keyCol}, stored_bytes, ${q.writesCol}, period) VALUES (?5, ?1, 1, ?2)
     ON CONFLICT(${q.keyCol}) DO UPDATE SET
       stored_bytes = stored_bytes + ?1,
       ${q.writesCol} = CASE WHEN period = ?2 THEN ${q.writesCol} + 1 ELSE 1 END,
       period = ?2
     WHERE stored_bytes + ?1 <= ?3
       AND (CASE WHEN period = ?2 THEN ${q.writesCol} ELSE 0 END) + 1 <= ?4
     RETURNING stored_bytes`,
  ).bind(bytes, q.period, q.maxBytes, q.maxWrites, q.key).first<{ stored_bytes: number }>();
  return row ? row.stored_bytes : null;
}

// Give back a reservation whose R2 write never happened.
async function release(env: Env, q: Quota, bytes: number): Promise<void> {
  await env.DB.prepare(
    `UPDATE ${q.table} SET stored_bytes = MAX(0, stored_bytes - ?1),
       ${q.writesCol} = CASE WHEN period = ?2 AND ${q.writesCol} > 0 THEN ${q.writesCol} - 1 ELSE ${q.writesCol} END
     WHERE ${q.keyCol} = ?3`,
  ).bind(bytes, q.period, q.key).run();
}

// Reserve against every counter in order, or none: on the first refusal, the ones already
// taken are given back. `stored` is the last counter's new byte total.
async function reserveAll(
  env: Env, quotas: Quota[], bytes: number,
): Promise<{ ok: true; stored: number } | { ok: false; refused: Quota }> {
  let stored = 0;
  for (let i = 0; i < quotas.length; i++) {
    const got = await reserve(env, quotas[i], bytes);
    if (got === null) {
      for (const q of quotas.slice(0, i)) await release(env, q, bytes);
      return { ok: false, refused: quotas[i] };
    }
    stored = got;
  }
  return { ok: true, stored };
}

async function releaseAll(env: Env, quotas: Quota[], bytes: number): Promise<void> {
  for (const q of quotas) await release(env, q, bytes);
}

// Which of the refused counter's caps was hit: storage (507) or writes (429).
async function quotaError(env: Env, q: Quota, bytes: number): Promise<Response> {
  const row = await env.DB.prepare(
    `SELECT stored_bytes FROM ${q.table} WHERE ${q.keyCol} = ?`,
  ).bind(q.key).first<{ stored_bytes: number }>();
  const full = bytes > q.maxBytes || (row?.stored_bytes ?? 0) + bytes > q.maxBytes;
  if (q.table === "publisher_usage") {
    const mb = q.maxBytes / 1e6;
    return full
      ? json({ error: `you've used your ${mb >= 10 ? Math.round(mb) : mb.toFixed(1)} MB of marketplace storage` }, 507)
      : json({ error: `you've published ${q.maxWrites} versions today; try again tomorrow (UTC)` }, 429);
  }
  return full
    ? json({ error: `${q.label} storage cap reached` }, 507)
    : json({ error: `${q.label} monthly write cap reached` }, 429);
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json", ...CORS },
  });
}

// R2 object key for a bundle: one object per published version. Versions never change once
// published, so nothing ever writes to an existing key. (Rows from before this used
// "bundles/<content hash>.olx"; each row's r2_key says where its blob is.)
function bundleKey(namespace: string, name: string, version: string): string {
  return `bundles/${namespace}/${name}/${version}.olx`;
}

const HANDLE_RE = /^[a-z0-9_-]{2,64}$/;
// A bundle's id, as olisar.extensions.bundle.KEY_RE requires; the bot refuses anything else.
const EXT_ID_RE = /^[a-z][a-z0-9_]{1,63}$/;
// A version, as the bot's marketplace references allow (api/routers/marketplace.py _VER_RE).
const VERSION_RE = /^[A-Za-z0-9._-]{1,32}$/;

// Registering (or rotating a token) needs proof the caller holds the private half of
// `public_key`: it signs registerMessage(nonce, handle) over a single-use nonce from
// /v1/publishers/challenge. The "olisar-registry/register:" prefix means the message can
// never equal a bundle's content_hash, so a signature lifted from a published bundle
// can't be replayed as proof. Must match olisar.extensions.signing.register_message.
const CHALLENGE_TTL_SECONDS = 300;
const NONCE_RE = /^[0-9a-f]{64}$/;
function registerMessage(nonce: string, handle: string): string {
  return `olisar-registry/register:${nonce}:${handle}`;
}

function b64bytes(s: string): Uint8Array {
  return Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
}
function hex(buf: ArrayBuffer): string {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
async function sha256hex(input: string | Uint8Array): Promise<string> {
  const data = typeof input === "string" ? new TextEncoder().encode(input) : input;
  return hex(await crypto.subtle.digest("SHA-256", data));
}
// A bundle's content hash, computed here rather than taken from the caller. Must match
// olisar.extensions.bundle.canonical_hash byte for byte, since publishers sign the bot's
// hash and every installing bot recomputes it. Python's json.dumps(sort_keys=True,
// separators=(",", ":"), ensure_ascii=False) writes the same text JSON.stringify does for
// well-formed strings, given the keys in sorted order and permissions sorted by code point
// (Python's order; JS's default sort compares UTF-16 units, which differs past U+FFFF).
async function canonicalHash(id: string, version: string, source: string, permissions: string[]): Promise<string> {
  const payload = JSON.stringify({ id, permissions: [...permissions].sort(byCodePoint), source, version });
  return "sha256:" + (await sha256hex(payload));
}
function byCodePoint(a: string, b: string): number {
  const x = [...a];
  const y = [...b];
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const d = x[i].codePointAt(0)! - y[i].codePointAt(0)!;
    if (d) return d;
  }
  return x.length - y.length;
}
// A lone surrogate is the one thing the two JSON encoders disagree on (JS escapes it, and
// Python can't encode it to UTF-8 at all), so such text is refused rather than hashed.
function wellFormed(s: string): boolean {
  return !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(s);
}

// Validate the fields the content hash covers and compute it. The caller's content_hash has
// to equal ours: it's what the publisher signed and what installing bots check.
async function checkBundle(bundle: any): Promise<{ name: string; version: string; hash: string } | Response> {
  if (!bundle || typeof bundle !== "object") return json({ error: "bad bundle" }, 400);
  const { id, version, source } = bundle;
  const permissions = bundle.permissions ?? [];
  if (typeof id !== "string" || !EXT_ID_RE.test(id)) {
    return json({ error: "bad bundle id (lowercase letters, digits and _, starting with a letter)" }, 400);
  }
  if (typeof version !== "string" || !VERSION_RE.test(version)) {
    return json({ error: "bad bundle version (1-32 chars: letters, digits, '.', '_' or '-')" }, 400);
  }
  if (typeof source !== "string" || !source.trim()) return json({ error: "bundle has no source" }, 400);
  if (!Array.isArray(permissions) || !permissions.every((p: unknown) => typeof p === "string")) {
    return json({ error: "bundle permissions must be a list of strings" }, 400);
  }
  if (![source, ...permissions].every(wellFormed)) {
    return json({ error: "bundle text isn't valid Unicode" }, 400);
  }
  const hash = await canonicalHash(id, version, source, permissions);
  if (bundle.content_hash !== hash) {
    return json({ error: "content_hash doesn't match the bundle's id, version, permissions and source" }, 400);
  }
  return { name: id, version, hash };
}

// Matches olisar.extensions.signing.fingerprint: "sha256:" + sha256(pubkey)[:32].
async function fingerprintOf(pubB64: string): Promise<string> {
  return "sha256:" + (await sha256hex(b64bytes(pubB64))).slice(0, 32);
}
async function verifyEd25519(pubB64: string, message: string, sigB64: string): Promise<boolean> {
  try {
    const key = await crypto.subtle.importKey("raw", b64bytes(pubB64), { name: "Ed25519" }, false, ["verify"]);
    return await crypto.subtle.verify({ name: "Ed25519" }, key, b64bytes(sigB64), new TextEncoder().encode(message));
  } catch {
    return false;
  }
}
function randomToken(): string {
  const a = new Uint8Array(32);
  crypto.getRandomValues(a);
  return [...a].map((b) => b.toString(16).padStart(2, "0")).join("");
}
async function publisherForToken(env: Env, req: Request): Promise<any | null> {
  const auth = req.headers.get("authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
  if (!token) return null;
  return env.DB.prepare(
    "SELECT id, handle, public_key, verified, discord_id FROM publishers WHERE token_hash = ?",
  ).bind(await sha256hex(token)).first();
}

// A developer (platform moderator) is a publisher whose Discord id is in the `developers`
// allowlist AND was set by Discord itself (/v1/publishers/verify, which sets verified = 1).
// An unverified discord_id is a self-asserted leftover from the old register flow and
// never grants anything. The gate distinguishes an unknown/stale token (401, so the bot
// re-registers and retries) from a valid token that simply isn't a developer (403).
async function isDeveloper(env: Env, pub: any): Promise<boolean> {
  if (!pub || Number(pub.verified) !== 1 || !pub.discord_id) return false;
  const dev = await env.DB.prepare("SELECT discord_id FROM developers WHERE discord_id = ?")
    .bind(String(pub.discord_id)).first();
  return !!dev;
}

async function requireDeveloper(env: Env, req: Request): Promise<{ pub?: any; resp?: Response }> {
  const pub = await publisherForToken(env, req);
  if (!pub) return { resp: json({ error: "unauthorized" }, 401) };
  if (!(await isDeveloper(env, pub))) {
    return { resp: json({ error: "not a developer" }, 403) };
  }
  return { pub };
}

function entryFromRow(r: any) {
  return {
    namespace: r.namespace,
    name: r.name,
    id: `${r.namespace}/${r.name}`,
    category: r.category,
    description: r.description,
    version: r.latest_version,
    downloads: r.downloads ?? 0,
    permissions: r.permissions ? JSON.parse(r.permissions) : [],
    sdk_version: r.sdk_version ?? null,
    risk_score: r.risk_score ?? null,
    risk_report: r.risk_report ? safeJson(r.risk_report) : null,
    publisher: r.publisher ?? null,
    publisher_fingerprint: r.publisher_fingerprint ?? null,
    publisher_verified: !!r.publisher_verified,
  };
}

function safeJson(s: string): any {
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}

export default {
  async fetch(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(req.url);
    const parts = url.pathname.split("/").filter(Boolean); // e.g. ["v1","ext","ns","name"]
    try {
      if (req.method === "GET" && url.pathname === "/v1/health") {
        return json({ ok: true });
      }
      if (req.method === "GET" && url.pathname === "/v1/search") {
        return await search(url, env);
      }
      if (req.method === "GET" && parts[0] === "v1" && parts[1] === "ext") {
        if (parts.length === 4) return await detail(parts[2], parts[3], env);
        if (parts.length === 5) return await getBundle(parts[2], parts[3], parts[4], env);
      }
      if (req.method === "POST" && url.pathname === "/v1/publishers/challenge") {
        return await publishersChallenge(env);
      }
      if (req.method === "POST" && url.pathname === "/v1/publishers/register") {
        return await publishersRegister(req, env);
      }
      if (req.method === "POST" && url.pathname === "/v1/publishers/verify") {
        return await publisherVerify(req, env);
      }
      if (req.method === "POST" && url.pathname === "/v1/publish") {
        return await publisherPublish(req, env);
      }
      if (req.method === "POST" && url.pathname === "/v1/yank") {
        return await publisherYank(req, env);
      }
      // Abuse reports, install counting, and moderation standing (consumed by every bot).
      if (req.method === "POST" && url.pathname === "/v1/report") {
        return await fileReport(req, env);
      }
      if (req.method === "POST" && url.pathname === "/v1/feedback") {
        return await fileFeedback(req, env);
      }
      if (req.method === "POST" && url.pathname === "/v1/install") {
        return await bumpInstall(req, env);
      }
      if (req.method === "POST" && url.pathname === "/v1/blocked") {
        return await recordBlocked(req, env);
      }
      if (req.method === "GET" && url.pathname === "/v1/standing") {
        return await standing(url, env);
      }
      if (req.method === "POST" && url.pathname === "/v1/standing/ack") {
        return await standingAck(req, env);
      }
      if (req.method === "GET" && url.pathname === "/v1/moderation/bans") {
        return await moderationBans(env);
      }
      // Developer (platform-moderator) management — every route token-gated to a whitelisted dev.
      if (req.method === "GET" && url.pathname === "/v1/dev/me") {
        return await devMe(req, env);
      }
      if (req.method === "GET" && url.pathname === "/v1/dev/extensions") {
        return await devExtensions(req, env);
      }
      if (req.method === "GET" && url.pathname === "/v1/dev/source") {
        return await devSource(url, req, env);
      }
      if (req.method === "GET" && url.pathname === "/v1/dev/reports") {
        return await devReports(req, env);
      }
      if (req.method === "GET" && url.pathname === "/v1/dev/blocked") {
        return await devBlocked(req, env);
      }
      if (req.method === "GET" && url.pathname === "/v1/dev/moderation") {
        return await devModerationList(req, env);
      }
      if (req.method === "POST" && url.pathname === "/v1/dev/yank") {
        return await devYank(req, env);
      }
      if (req.method === "POST" && url.pathname === "/v1/dev/moderation") {
        return await devModeration(req, env);
      }
      if (req.method === "POST" && url.pathname === "/v1/dev/reports/clear") {
        return await devClearReports(req, env);
      }
      if (req.method === "POST" && url.pathname === "/v1/dev/blocked/clear") {
        return await devClearBlocked(req, env);
      }
      if (req.method === "POST" && url.pathname === "/v1/admin/publish") {
        return await adminPublish(req, env);
      }
      if (req.method === "POST" && url.pathname === "/v1/_dev/publish") {
        if (env.DEV_SEED !== "1") return json({ error: "not found" }, 404);
        return await publishFromBody(req, env);
      }
      return json({ error: "not found" }, 404);
    } catch (err: any) {
      return json({ error: String(err?.message || err) }, 500);
    }
  },
} satisfies ExportedHandler<Env>;

async function search(url: URL, env: Env): Promise<Response> {
  const q = (url.searchParams.get("q") || "").trim();
  const cat = (url.searchParams.get("category") || "").trim();
  const limit = Math.min(50, Math.max(1, Number(url.searchParams.get("limit") || 30)));
  const offset = Math.max(0, Number(url.searchParams.get("offset") || 0));

  let sql = `SELECT e.namespace, e.name, e.category, e.description, e.latest_version, e.downloads,
      p.handle AS publisher, p.fingerprint AS publisher_fingerprint, p.verified AS publisher_verified,
      v.permissions AS permissions, v.sdk_version AS sdk_version,
      v.risk_score AS risk_score, v.risk_report AS risk_report
    FROM extensions e
    LEFT JOIN publishers p ON p.id = e.publisher_id
    LEFT JOIN versions v ON v.namespace = e.namespace AND v.name = e.name AND v.version = e.latest_version
    WHERE e.status = 'published' AND COALESCE(v.yanked, 0) = 0`;
  const binds: any[] = [];
  if (q) {
    sql += " AND (e.name LIKE ? OR e.description LIKE ?)";
    binds.push(`%${q}%`, `%${q}%`);
  }
  if (cat) {
    sql += " AND e.category = ?";
    binds.push(cat);
  }
  sql += " ORDER BY e.downloads DESC, e.updated_at DESC LIMIT ? OFFSET ?";
  binds.push(limit, offset);

  const { results } = await env.DB.prepare(sql).bind(...binds).all();
  return json({ results: (results || []).map(entryFromRow) });
}

async function detail(ns: string, name: string, env: Env): Promise<Response> {
  const ext = await env.DB.prepare(
    `SELECT e.*, p.handle AS publisher, p.fingerprint AS publisher_fingerprint, p.verified AS publisher_verified,
            v.risk_score AS risk_score, v.risk_report AS risk_report
     FROM extensions e LEFT JOIN publishers p ON p.id = e.publisher_id
     LEFT JOIN versions v ON v.namespace = e.namespace AND v.name = e.name AND v.version = e.latest_version
     WHERE e.namespace = ? AND e.name = ?`,
  ).bind(ns, name).first<any>();
  if (!ext) return json({ error: "not found" }, 404);

  const { results: versions } = await env.DB.prepare(
    `SELECT version, content_hash, sdk_version, permissions, signature, publisher_key, risk_score, risk_report, yanked, published_at
     FROM versions WHERE namespace = ? AND name = ? ORDER BY published_at DESC`,
  ).bind(ns, name).all();

  return json({
    ...entryFromRow(ext),
    status: ext.status,
    versions: (versions || []).map((v: any) => ({
      version: v.version,
      content_hash: v.content_hash,
      sdk_version: v.sdk_version,
      permissions: v.permissions ? JSON.parse(v.permissions) : [],
      risk_score: v.risk_score ?? null,
      risk_report: v.risk_report ? safeJson(v.risk_report) : null,
      signed: !!v.signature,
      yanked: !!v.yanked,
      published_at: v.published_at,
    })),
  });
}

async function getBundle(
  ns: string,
  name: string,
  version: string,
  env: Env,
): Promise<Response> {
  const row = await env.DB.prepare(
    `SELECT r2_key, yanked FROM versions WHERE namespace = ? AND name = ? AND version = ?`,
  ).bind(ns, name, version).first<{ r2_key: string; yanked: number }>();
  if (!row) return json({ error: "not found" }, 404);

  const obj = await env.BUNDLES.get(row.r2_key);
  if (!obj) return json({ error: "bundle blob missing" }, 404);

  // No per-read D1 write here: it would tie reads 1:1 to D1 writes and burn that
  // budget at scale. R2 read ops (Class B) stay under the free tier via the Workers
  // free-plan request cap. Download analytics can come later via Analytics Engine.
  return new Response(obj.body, {
    headers: {
      "content-type": "application/json",
      "x-olx-yanked": row.yanked ? "1" : "0",
      ...CORS,
    },
  });
}

// ── publishing ─────────────────────────────────────────────────────────────
// /v1/admin/publish (bearer-token gated, to seed a deployed registry) and the
// local-only /v1/_dev/publish both land in publishFromBody → storePublish, which
// enforces the free-tier storage + write caps. This is a stand-in for the full
// publish pipeline (Discord OAuth + signature/namespace verification), a later phase.
let schemaReady = false;
async function ensureSchema(env: Env): Promise<void> {
  if (schemaReady) return;
  await env.DB.batch([
    env.DB.prepare(
      `CREATE TABLE IF NOT EXISTS publishers (
         id INTEGER PRIMARY KEY AUTOINCREMENT, discord_id TEXT, handle TEXT NOT NULL,
         public_key TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE,
         verified INTEGER NOT NULL DEFAULT 0, token_hash TEXT,
         created_at TEXT NOT NULL DEFAULT (datetime('now')))`,
    ),
    env.DB.prepare(
      `CREATE TABLE IF NOT EXISTS extensions (
         namespace TEXT NOT NULL, name TEXT NOT NULL, publisher_id INTEGER,
         category TEXT, description TEXT, latest_version TEXT,
         downloads INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'published',
         created_at TEXT NOT NULL DEFAULT (datetime('now')),
         updated_at TEXT NOT NULL DEFAULT (datetime('now')),
         PRIMARY KEY (namespace, name))`,
    ),
    env.DB.prepare(
      `CREATE TABLE IF NOT EXISTS versions (
         id INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT NOT NULL, name TEXT NOT NULL,
         version TEXT NOT NULL, content_hash TEXT NOT NULL, r2_key TEXT NOT NULL,
         sdk_version TEXT, permissions TEXT, signature TEXT, publisher_key TEXT,
         yanked INTEGER NOT NULL DEFAULT 0, published_at TEXT NOT NULL DEFAULT (datetime('now')),
         UNIQUE (namespace, name, version))`,
    ),
    env.DB.prepare(
      `CREATE TABLE IF NOT EXISTS usage (
         id INTEGER PRIMARY KEY, stored_bytes INTEGER NOT NULL DEFAULT 0,
         class_a INTEGER NOT NULL DEFAULT 0, period TEXT NOT NULL DEFAULT '')`,
    ),
    env.DB.prepare(
      `CREATE TABLE IF NOT EXISTS developers (
         discord_id TEXT PRIMARY KEY, note TEXT,
         added_at TEXT NOT NULL DEFAULT (datetime('now')))`,
    ),
    env.DB.prepare(
      `CREATE TABLE IF NOT EXISTS moderation (
         discord_id TEXT PRIMARY KEY, status TEXT NOT NULL, message TEXT,
         acknowledged INTEGER NOT NULL DEFAULT 0,
         updated_at TEXT NOT NULL DEFAULT (datetime('now')))`,
    ),
    env.DB.prepare(
      `CREATE TABLE IF NOT EXISTS reports (
         id INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT NOT NULL, name TEXT NOT NULL,
         version TEXT, publisher_id INTEGER, publisher_discord_id TEXT,
         reporter_discord_id TEXT, description TEXT, logs_r2_key TEXT, attachments_r2_key TEXT,
         status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL DEFAULT (datetime('now')))`,
    ),
    env.DB.prepare(
      `CREATE TABLE IF NOT EXISTS blocked_publishes (
         id INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT, name TEXT NOT NULL, version TEXT,
         reporter_discord_id TEXT, risk_score INTEGER, threshold INTEGER, bullets TEXT,
         created_at TEXT NOT NULL DEFAULT (datetime('now')))`,
    ),
    env.DB.prepare(
      `CREATE TABLE IF NOT EXISTS publisher_challenges (
         nonce TEXT PRIMARY KEY, expires_at INTEGER NOT NULL)`,
    ),
    env.DB.prepare(
      `CREATE INDEX IF NOT EXISTS idx_publisher_challenges_expiry ON publisher_challenges (expires_at)`,
    ),
    env.DB.prepare(
      `CREATE TABLE IF NOT EXISTS publisher_usage (
         publisher_id INTEGER PRIMARY KEY, stored_bytes INTEGER NOT NULL DEFAULT 0,
         publishes INTEGER NOT NULL DEFAULT 0, period TEXT NOT NULL DEFAULT '')`,
    ),
  ]);
  // Add columns to tables that predate them (no-op once present). migrations/ has the same.
  for (const col of ["versions ADD COLUMN risk_score INTEGER", "versions ADD COLUMN risk_report TEXT",
                     "extensions ADD COLUMN yanked_by TEXT"]) {
    try {
      await env.DB.prepare(`ALTER TABLE ${col}`).run();
    } catch {
      /* column already exists */
    }
  }
  schemaReady = true;
}

async function adminPublish(req: Request, env: Env): Promise<Response> {
  const auth = req.headers.get("authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
  if (!env.ADMIN_TOKEN || token.length === 0 || token !== env.ADMIN_TOKEN) {
    return json({ error: "unauthorized" }, 401);
  }
  return publishFromBody(req, env);
}

async function publishFromBody(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const body = await req.json<any>();
  const checked = await checkBundle(body?.bundle);
  if (checked instanceof Response) return checked;
  const pub = body.publisher || {};
  const namespace = String(body.namespace || pub.handle || "demo");
  if (!HANDLE_RE.test(namespace)) return json({ error: "bad namespace" }, 400);
  return storePublish(env, namespace, pub, body.bundle, checked);
}

// `ownerId` is set on the self-serve path (the authenticated publisher's own row, which a
// publish never rewrites). Without it, `pub` describes a publisher row to upsert; only the
// ADMIN_TOKEN and local DEV_SEED paths do that, and they're trusted to set every field.
//
// A published version is immutable: its row and blob never change, and neither does its
// yanked flag once set. Publishing a version that already exists with the same content is a
// no-op; with different content it's refused. An extension a moderator yanked, or one
// de-listed by its publisher's ban, takes no new versions. One its publisher yanked comes
// back when they publish a new version (the yanked versions stay yanked).
async function storePublish(
  env: Env, namespace: string, pub: any, bundle: any,
  checked: { name: string; version: string; hash: string }, ownerId: number | null = null,
): Promise<Response> {
  const { name, version, hash } = checked;
  const id = `${namespace}/${name}`;
  const blob = JSON.stringify(bundle);
  const size = new TextEncoder().encode(blob).length;
  const lim = limits(env);
  if (size > lim.maxBundle) {
    return json({ error: `bundle too large (${size} > ${lim.maxBundle} bytes)` }, 413);
  }

  const ext = await env.DB.prepare(
    "SELECT publisher_id, status, yanked_by FROM extensions WHERE namespace = ? AND name = ?",
  ).bind(namespace, name).first<{ publisher_id: number | null; status: string; yanked_by: string | null }>();
  if (ext && ownerId !== null && ext.publisher_id != null && Number(ext.publisher_id) !== ownerId) {
    return json({ error: `${id} belongs to another publisher` }, 403);
  }
  if (ext && (ext.status === "banned" || (ext.status === "yanked" && ext.yanked_by !== "publisher"))) {
    return json({ error: `${id} was removed from the marketplace and can't take new versions` }, 403);
  }
  const alreadyPublished = () => json({
    error: `${id} ${version} is already published, and a published version can't change; `
      + "bump the version to publish this",
  }, 409);
  const existing = await env.DB.prepare(
    "SELECT content_hash, yanked FROM versions WHERE namespace = ? AND name = ? AND version = ?",
  ).bind(namespace, name, version).first<{ content_hash: string; yanked: number }>();
  if (existing) {
    if (existing.yanked) {
      return json({ error: `${id} ${version} was yanked; publish a new version instead` }, 409);
    }
    if (existing.content_hash === hash) return json({ ok: true, id, version, unchanged: true });
    return alreadyPublished();
  }

  // Claim the version before writing anything. versions is UNIQUE (namespace, name, version),
  // so of two publishes of the same new version exactly one gets the row, and only that one
  // writes the blob.
  const key = bundleKey(namespace, name, version);
  const riskScore = Number.isFinite(Number(bundle.risk_score)) ? Math.round(Number(bundle.risk_score)) : null;
  const riskReport = bundle.risk_report != null ? JSON.stringify(bundle.risk_report) : null;
  const claim = await env.DB.prepare(
    `INSERT INTO versions (namespace, name, version, content_hash, r2_key, sdk_version, permissions, signature, publisher_key, risk_score, risk_report)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT(namespace, name, version) DO NOTHING
     RETURNING id`,
  ).bind(
    namespace, name, version, hash, key,
    bundle.sdk_version ?? "1", JSON.stringify(bundle.permissions ?? []),
    bundle.signature ?? null, bundle.public_key ?? null, riskScore, riskReport,
  ).first<{ id: number }>();
  if (!claim) return alreadyPublished();
  const unclaim = () => env.DB.prepare("DELETE FROM versions WHERE id = ?").bind(claim.id).run();

  // Free-tier guard, and the publisher's own share of it, reserved before the R2 write.
  const quotas = ownerId !== null ? [publisherQuota(env, ownerId), globalQuota(env)] : [globalQuota(env)];
  const reserved = await reserveAll(env, quotas, size);
  if (!reserved.ok) {
    await unclaim();
    return quotaError(env, reserved.refused, size);
  }
  try {
    await env.BUNDLES.put(key, blob, { httpMetadata: { contentType: "application/json" } });
  } catch (err) {
    await releaseAll(env, quotas, size);
    await unclaim();
    throw err;
  }

  let publisherId: number | null = ownerId;
  if (ownerId === null && pub.fingerprint) {
    await env.DB.prepare(
      `INSERT INTO publishers (discord_id, handle, public_key, fingerprint, verified)
       VALUES (?, ?, ?, ?, ?)
       ON CONFLICT(fingerprint) DO UPDATE SET handle = excluded.handle, verified = excluded.verified`,
    ).bind(pub.discord_id ?? null, pub.handle ?? namespace, pub.public_key ?? "", pub.fingerprint, pub.verified ? 1 : 0).run();
    const prow = await env.DB.prepare(`SELECT id FROM publishers WHERE fingerprint = ?`)
      .bind(pub.fingerprint).first<{ id: number }>();
    publisherId = prow ? prow.id : null;
  }

  // Only the publisher's own yank lifts here (re-checked in SQL in case a moderator yanked
  // or a ban landed since the check above); anything else keeps its status.
  await env.DB.prepare(
    `INSERT INTO extensions (namespace, name, publisher_id, category, description, latest_version, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
     ON CONFLICT(namespace, name) DO UPDATE SET
       publisher_id = excluded.publisher_id, category = excluded.category,
       description = excluded.description, latest_version = excluded.latest_version,
       status = CASE WHEN status = 'yanked' AND yanked_by = 'publisher' THEN 'published' ELSE status END,
       yanked_by = CASE WHEN status = 'yanked' AND yanked_by = 'publisher' THEN NULL ELSE yanked_by END,
       updated_at = datetime('now')`,
  ).bind(namespace, name, publisherId, bundle.category ?? "General", bundle.description ?? "", version).run();

  return json({ ok: true, id, version, stored_bytes: reserved.stored });
}

// ── self-serve publishing ──────────────────────────────────────────────────
// Issue a single-use nonce for /v1/publishers/register. Expired nonces are pruned here, so
// the table only ever holds the last few minutes' worth.
async function publishersChallenge(env: Env): Promise<Response> {
  await ensureSchema(env);
  const now = Math.floor(Date.now() / 1000);
  const nonce = randomToken();
  const expiresAt = now + CHALLENGE_TTL_SECONDS;
  await env.DB.batch([
    env.DB.prepare("DELETE FROM publisher_challenges WHERE expires_at < ?").bind(now),
    env.DB.prepare("INSERT INTO publisher_challenges (nonce, expires_at) VALUES (?, ?)")
      .bind(nonce, expiresAt),
  ]);
  return json({ nonce, expires_at: expiresAt });
}

// Register a publisher: a handle (namespace) bound to an Ed25519 public key, first come
// first served. Re-registering with the same key rotates the token. Either way the caller
// must sign registerMessage(nonce, handle) with that key, so knowing a public key (every
// signed bundle carries one) isn't enough to claim it or rotate its owner's token out.
// A discord_id in the body is ignored: only /v1/publishers/verify, which asks Discord,
// ever sets it. A new row starts with no discord_id and verified = 0.
async function publishersRegister(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  let body: any;
  try {
    body = await req.json<any>();
  } catch {
    return json({ error: "expected a JSON body" }, 400);
  }
  const pub = String(body?.public_key || "");
  const handle = String(body?.handle || "").toLowerCase();
  const nonce = String(body?.nonce || "");
  const signature = String(body?.signature || "");
  if (!pub || !HANDLE_RE.test(handle)) {
    return json({ error: "a public_key and a handle (2-64 chars, [a-z0-9_-]) are required" }, 400);
  }
  if (!NONCE_RE.test(nonce) || !signature) {
    return json({
      error: "registering needs a signed challenge (nonce + signature from POST "
        + "/v1/publishers/challenge); update Olisar and try again",
    }, 400);
  }
  // Burn the nonce before checking the proof, so each one gets exactly one attempt.
  const now = Math.floor(Date.now() / 1000);
  const used = await env.DB.prepare(
    "DELETE FROM publisher_challenges WHERE nonce = ? AND expires_at >= ?",
  ).bind(nonce, now).run();
  if ((used.meta?.changes ?? 0) !== 1) {
    return json({ error: "unknown, expired, or already-used challenge; request a new one" }, 403);
  }
  if (!(await verifyEd25519(pub, registerMessage(nonce, handle), signature))) {
    return json({ error: "the signature doesn't prove ownership of this public_key" }, 403);
  }
  const fp = await fingerprintOf(pub);
  const existing = await env.DB.prepare("SELECT discord_id FROM publishers WHERE fingerprint = ?")
    .bind(fp).first<{ discord_id: string | null }>();
  if (existing?.discord_id && (await isBanned(env, String(existing.discord_id)))) {
    return json({ error: "this account is banned from the marketplace" }, 403);
  }
  const owner = await env.DB.prepare("SELECT fingerprint FROM publishers WHERE handle = ?")
    .bind(handle).first<{ fingerprint: string }>();
  if (owner && owner.fingerprint !== fp) {
    return json({ error: `the handle '${handle}' is already taken` }, 409);
  }
  const token = randomToken();
  // Rotation keeps discord_id and verified exactly as they were.
  await env.DB.prepare(
    `INSERT INTO publishers (discord_id, handle, public_key, fingerprint, verified, token_hash)
     VALUES (NULL, ?, ?, ?, 0, ?)
     ON CONFLICT(fingerprint) DO UPDATE SET handle = excluded.handle,
       token_hash = excluded.token_hash`,
  ).bind(handle, pub, fp, await sha256hex(token)).run();
  return json({ ok: true, handle, fingerprint: fp, token });
}

// Publish a signed bundle under the authenticated publisher's namespace. The signature
// must be by the publisher's registered key — so a handle can only ship code its key
// owner signed (the installing bot independently re-verifies on download).
async function publisherPublish(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const publisher = await publisherForToken(env, req);
  if (!publisher) return json({ error: "unauthorized" }, 401);
  if (publisher.discord_id && (await isBanned(env, String(publisher.discord_id)))) {
    return json({ error: "this publisher is banned from the marketplace" }, 403);
  }
  const body = await req.json<any>();
  const bundle = body?.bundle;
  const checked = await checkBundle(bundle);
  if (checked instanceof Response) return checked;
  if (!bundle.public_key || bundle.public_key !== publisher.public_key) {
    return json({ error: "bundle is not signed by your publisher key" }, 403);
  }
  if (!bundle.signature || !(await verifyEd25519(publisher.public_key, checked.hash, bundle.signature))) {
    return json({ error: "invalid bundle signature" }, 403);
  }
  return storePublish(env, publisher.handle, {}, bundle, checked, Number(publisher.id));
}

// Bind a verified Discord identity to the authenticated publisher. The bot forwards the
// operator's short-lived `identify` token; the registry confirms it with Discord itself
// (so it never just trusts the bot's word) and sets the verified badge. This is the only
// self-serve route that writes a publisher's discord_id or verified flag.
async function publisherVerify(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const publisher = await publisherForToken(env, req);
  if (!publisher) return json({ error: "unauthorized" }, 401);
  // A ban follows the Discord id, so a banned publisher can't shed it by verifying again
  // with a different account.
  if (publisher.discord_id && (await isBanned(env, String(publisher.discord_id)))) {
    return json({ error: "this publisher is banned from the marketplace" }, 403);
  }
  const body = await req.json<any>();
  const discordToken = String(body?.discord_token || "");
  if (!discordToken) return json({ error: "discord_token required" }, 400);
  let me: any;
  try {
    const r = await fetch("https://discord.com/api/users/@me", {
      headers: { authorization: "Bearer " + discordToken },
    });
    if (r.status !== 200) return json({ error: "Discord verification failed" }, 401);
    me = await r.json();
  } catch (e: any) {
    return json({ error: "couldn't reach Discord" }, 502);
  }
  const discordId = String(me?.id || "");
  if (!discordId) return json({ error: "Discord returned no user id" }, 401);
  await env.DB.prepare("UPDATE publishers SET discord_id = ?, verified = 1 WHERE id = ?")
    .bind(discordId, publisher.id).run();
  return json({ ok: true, discord_id: discordId, username: me?.username || me?.global_name || null, verified: true });
}

async function publisherYank(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const publisher = await publisherForToken(env, req);
  if (!publisher) return json({ error: "unauthorized" }, 401);
  const body = await req.json<any>();
  const name = String(body?.name || "");
  const version = body?.version ? String(body.version) : null;
  if (!name) return json({ error: "name required" }, 400);
  await yankExtension(env, publisher.handle, name, version, "publisher");
  return json({ ok: true });
}

// A yanked version stays yanked: nothing clears the flag, and a version can't be published
// again. Yanking a whole extension also records who did it. A moderator's yank always wins
// and a publisher's never replaces an earlier one, so only an extension its own publisher
// yanked (and no moderator since) comes back when the publisher publishes a new version.
// Yanks from before yanked_by existed stay NULL and count as the moderator's.
async function yankExtension(
  env: Env, namespace: string, name: string, version: string | null, by: "publisher" | "moderator",
): Promise<void> {
  if (version) {
    await env.DB.prepare("UPDATE versions SET yanked = 1 WHERE namespace = ? AND name = ? AND version = ?")
      .bind(namespace, name, version).run();
  } else {
    await env.DB.batch([
      env.DB.prepare("UPDATE versions SET yanked = 1 WHERE namespace = ? AND name = ?")
        .bind(namespace, name),
      env.DB.prepare(
        `UPDATE extensions SET yanked_by = CASE
           WHEN ?1 = 'moderator' THEN 'moderator'
           WHEN status = 'yanked' THEN yanked_by
           ELSE 'publisher' END,
         status = 'yanked'
         WHERE namespace = ?2 AND name = ?3`,
      ).bind(by, namespace, name),
    ]);
  }
}

// ── moderation standing (consumed continuously by console + bot) ─────────────
async function isBanned(env: Env, discordId: string): Promise<boolean> {
  if (!discordId) return false;
  const row = await env.DB.prepare("SELECT status FROM moderation WHERE discord_id = ?")
    .bind(discordId).first<{ status: string }>();
  return !!row && row.status === "banned";
}

async function standing(url: URL, env: Env): Promise<Response> {
  await ensureSchema(env);
  const discordId = (url.searchParams.get("discord_id") || "").trim();
  if (!discordId) return json({ status: "ok" });
  const row = await env.DB.prepare(
    "SELECT status, message, acknowledged FROM moderation WHERE discord_id = ?",
  ).bind(discordId).first<{ status: string; message: string; acknowledged: number }>();
  if (!row) return json({ status: "ok" });
  return json({ status: row.status, message: row.message || "", acknowledged: !!row.acknowledged });
}

async function standingAck(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const body = await req.json<any>();
  const discordId = String(body?.discord_id || "").trim();
  if (!discordId) return json({ error: "discord_id required" }, 400);
  await env.DB.prepare("UPDATE moderation SET acknowledged = 1 WHERE discord_id = ? AND status = 'warned'")
    .bind(discordId).run();
  return json({ ok: true });
}

// The current ban list — a minimal blocklist (ids only) bots sync periodically.
async function moderationBans(env: Env): Promise<Response> {
  await ensureSchema(env);
  const { results } = await env.DB.prepare("SELECT discord_id FROM moderation WHERE status = 'banned'").all();
  return json({ bans: (results || []).map((r: any) => String(r.discord_id)) });
}

// ── install counting ─────────────────────────────────────────────────────────
async function bumpInstall(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const body = await req.json<any>();
  const namespace = String(body?.namespace || "");
  const name = String(body?.name || "");
  if (!namespace || !name) return json({ error: "namespace and name required" }, 400);
  await env.DB.prepare("UPDATE extensions SET downloads = downloads + 1 WHERE namespace = ? AND name = ?")
    .bind(namespace, name).run();
  return json({ ok: true });
}

// ── blocked publishes (recorded by a bot when its risk review blocks a publish) ──
async function recordBlocked(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const body = await req.json<any>();
  const name = String(body?.name || "").trim();
  if (!name) return json({ error: "name required" }, 400);
  await env.DB.prepare(
    `INSERT INTO blocked_publishes (namespace, name, version, reporter_discord_id, risk_score, threshold, bullets)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
  ).bind(
    body.namespace ? String(body.namespace) : null,
    name,
    body.version ? String(body.version) : null,
    body.reporter_discord_id ? String(body.reporter_discord_id) : null,
    Number.isFinite(Number(body.risk_score)) ? Math.round(Number(body.risk_score)) : null,
    Number.isFinite(Number(body.threshold)) ? Math.round(Number(body.threshold)) : null,
    body.bullets != null ? JSON.stringify(body.bullets) : null,
  ).run();
  return json({ ok: true });
}

// ── abuse reports ────────────────────────────────────────────────────────────
const REPORT_DESC_MAX = 8000;
const REPORT_LOGS_MAX = 120_000;
const REPORT_ATTACH_MAX = 3_500_000; // total decoded bytes across attachments

async function fileReport(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const body = await req.json<any>();
  const namespace = String(body?.namespace || "").trim();
  const name = String(body?.name || "").trim();
  if (!namespace || !name) return json({ error: "namespace and name required" }, 400);
  const version = body?.version ? String(body.version) : null;
  const reporter = body?.reporter_discord_id ? String(body.reporter_discord_id) : null;
  // Soft anti-flood: cap how many open reports one reporter can have outstanding.
  if (reporter) {
    const n = await env.DB.prepare(
      "SELECT COUNT(*) AS c FROM reports WHERE reporter_discord_id = ? AND status = 'open'",
    ).bind(reporter).first<{ c: number }>();
    if ((n?.c ?? 0) >= 20) return json({ error: "too many open reports — please wait for review" }, 429);
  }
  const description = String(body?.description || "").slice(0, REPORT_DESC_MAX);
  const logs = String(body?.logs || "").slice(0, REPORT_LOGS_MAX);
  const attachments = sanitizeAttachments(body?.attachments);

  // Resolve the publisher (the id the platform owner would warn/ban). Only a Discord-verified
  // id counts: an unverified one was self-asserted and could name anyone.
  const pub = await env.DB.prepare(
    `SELECT p.id AS id, p.handle AS handle,
            CASE WHEN p.verified = 1 THEN p.discord_id END AS discord_id
     FROM extensions e LEFT JOIN publishers p ON p.id = e.publisher_id
     WHERE e.namespace = ? AND e.name = ?`,
  ).bind(namespace, name).first<any>();

  const ins = await env.DB.prepare(
    `INSERT INTO reports (namespace, name, version, publisher_id, publisher_discord_id, reporter_discord_id, description)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
  ).bind(namespace, name, version, pub?.id ?? null, pub?.discord_id ?? null, reporter, description).run();
  const id = ins.meta?.last_row_id ?? 0;

  // Stash the bulky payload (logs + attachments) as a single R2 blob (one write). It counts
  // against the bucket's caps like a bundle does, within the share reports may use; past
  // that the report is still filed and emailed, just without the stored copy.
  if (logs || attachments.length) {
    const blobKey = `reports/${id}.json`;
    const blob = JSON.stringify({ logs, attachments });
    const size = new TextEncoder().encode(blob).length;
    const quotas = [reportQuota(env), globalQuota(env)];
    if ((await reserveAll(env, quotas, size)).ok) {
      try {
        await env.BUNDLES.put(blobKey, blob, { httpMetadata: { contentType: "application/json" } });
      } catch (err) {
        await releaseAll(env, quotas, size);
        throw err;
      }
      await env.DB.prepare("UPDATE reports SET logs_r2_key = ? WHERE id = ?").bind(blobKey, id).run();
    }
  }

  const email = await sendReportEmail(env, {
    id, namespace, name, version,
    publisherHandle: pub?.handle ?? null, publisherDiscordId: pub?.discord_id ?? null,
    reporterDiscordId: reporter, description, logs, attachments,
  });
  return json({ ok: true, id, emailed: email.ok, email });
}

function sanitizeAttachments(raw: any): { name: string; type: string; content_b64: string }[] {
  if (!Array.isArray(raw)) return [];
  const out: { name: string; type: string; content_b64: string }[] = [];
  let total = 0;
  for (const a of raw.slice(0, 8)) {
    const content = String(a?.content_b64 || a?.content || "");
    if (!content) continue;
    total += Math.floor((content.length * 3) / 4);
    if (total > REPORT_ATTACH_MAX) break;
    out.push({
      name: String(a?.name || "attachment").slice(0, 120),
      type: String(a?.type || "application/octet-stream").slice(0, 100),
      content_b64: content,
    });
  }
  return out;
}

async function sendReportEmail(env: Env, r: any): Promise<{ ok: boolean; status?: number; error?: string; skipped?: string }> {
  if (!env.RESEND_API_KEY) return { ok: false, skipped: "RESEND_API_KEY secret is not set" };
  if (!env.REPORT_EMAIL) return { ok: false, skipped: "REPORT_EMAIL secret is not set" };
  const lines = [
    `A marketplace extension was reported.`,
    ``,
    `Extension:   ${r.namespace}/${r.name}${r.version ? ` @ ${r.version}` : ""}`,
    `Publisher:   ${r.publisherHandle || "(unknown)"}`,
    `Publisher Discord ID (warn/ban): ${r.publisherDiscordId || "(unknown)"}`,
    `Reporter Discord ID:             ${r.reporterDiscordId || "(unknown)"}`,
    `Report #${r.id}`,
    ``,
    `What they reported:`,
    r.description || "(no description)",
  ];
  const attachments: { filename: string; content: string }[] = [];
  if (r.logs) attachments.push({ filename: "bot-logs.txt", content: btoa(unescape(encodeURIComponent(r.logs))) });
  for (const a of r.attachments || []) attachments.push({ filename: a.name, content: a.content_b64 });
  try {
    const resp = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: { authorization: `Bearer ${env.RESEND_API_KEY}`, "content-type": "application/json" },
      body: JSON.stringify({
        from: env.REPORT_FROM || "Olisar <onboarding@resend.dev>",
        to: [env.REPORT_EMAIL],
        subject: `[Olisar report] ${r.namespace}/${r.name}`,
        text: lines.join("\n"),
        attachments: attachments.length ? attachments : undefined,
      }),
    });
    if (resp.ok) return { ok: true, status: resp.status };
    let body = "";
    try { body = await resp.text(); } catch { /* ignore */ }
    console.log("resend send failed", resp.status, body);
    return { ok: false, status: resp.status, error: body.slice(0, 500) };
  } catch (e: any) {
    return { ok: false, error: String((e && e.message) || e) };
  }
}

// ── console feedback (feedback / bug report / question, emailed to the owner) ──
const FEEDBACK_MSG_MAX = 8000;

async function fileFeedback(req: Request, env: Env): Promise<Response> {
  const body = await req.json<any>();
  const message = String(body?.message || "").trim().slice(0, FEEDBACK_MSG_MAX);
  if (!message) return json({ error: "message required" }, 400);
  const category = String(body?.category || "Feedback").slice(0, 40);
  const email = String(body?.email || "").trim().slice(0, 200);
  const logs = String(body?.logs || "").slice(0, REPORT_LOGS_MAX);
  const attachments = sanitizeAttachments(body?.attachments);
  const sent = await sendFeedbackEmail(env, { category, message, email, logs, attachments });
  return json({ ok: true, emailed: sent.ok, email: sent });
}

async function sendFeedbackEmail(env: Env, f: any): Promise<{ ok: boolean; status?: number; error?: string; skipped?: string }> {
  if (!env.RESEND_API_KEY) return { ok: false, skipped: "RESEND_API_KEY secret is not set" };
  if (!env.REPORT_EMAIL) return { ok: false, skipped: "REPORT_EMAIL secret is not set" };
  const lines = [
    `New ${f.category} from the Olisar console.`,
    ``,
    `From:      ${f.email || "(no email given)"}`,
    `Category:  ${f.category}`,
    ``,
    f.message,
  ];
  const attachments: { filename: string; content: string }[] = [];
  if (f.logs) attachments.push({ filename: "bot-logs.txt", content: btoa(unescape(encodeURIComponent(f.logs))) });
  for (const a of f.attachments || []) attachments.push({ filename: a.name, content: a.content_b64 });
  const validEmail = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(f.email || "");
  try {
    const resp = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: { authorization: `Bearer ${env.RESEND_API_KEY}`, "content-type": "application/json" },
      body: JSON.stringify({
        from: env.REPORT_FROM || "Olisar <onboarding@resend.dev>",
        to: [env.REPORT_EMAIL],
        reply_to: validEmail ? f.email : undefined,
        subject: `[Olisar ${f.category}] ${f.message.slice(0, 60)}`,
        text: lines.join("\n"),
        attachments: attachments.length ? attachments : undefined,
      }),
    });
    if (resp.ok) return { ok: true, status: resp.status };
    let body = "";
    try { body = await resp.text(); } catch { /* ignore */ }
    console.log("resend feedback send failed", resp.status, body);
    return { ok: false, status: resp.status, error: body.slice(0, 500) };
  } catch (e: any) {
    return { ok: false, error: String((e && e.message) || e) };
  }
}

// ── developer (platform-moderator) routes ────────────────────────────────────
// Whether this token's publisher is a whitelisted developer (drives the console's
// Developer tab). Always 200 so the caller gets a clean boolean.
async function devMe(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const pub = await publisherForToken(env, req);
  if (!pub) return json({ error: "unauthorized" }, 401); // stale token → bot re-registers + retries
  const dev = await isDeveloper(env, pub);
  return json({
    is_developer: dev, handle: pub.handle, discord_id: pub.discord_id ?? null,
    verified: Number(pub.verified) === 1,
  });
}

async function devExtensions(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const gate = await requireDeveloper(env, req);
  if (gate.resp) return gate.resp;
  const { results } = await env.DB.prepare(
    `SELECT e.namespace, e.name, e.category, e.description, e.latest_version, e.downloads, e.status,
            e.created_at, e.updated_at,
            p.handle AS publisher,
            CASE WHEN p.verified = 1 THEN p.discord_id END AS publisher_discord_id,
            p.fingerprint AS publisher_fingerprint, p.verified AS publisher_verified,
            v.permissions, v.sdk_version, v.risk_score, v.risk_report, v.published_at
     FROM extensions e
     LEFT JOIN publishers p ON p.id = e.publisher_id
     LEFT JOIN versions v ON v.namespace = e.namespace AND v.name = e.name AND v.version = e.latest_version
     ORDER BY e.updated_at DESC`,
  ).all();
  return json({
    extensions: (results || []).map((r: any) => ({
      namespace: r.namespace, name: r.name, id: `${r.namespace}/${r.name}`,
      category: r.category, description: r.description,
      version: r.latest_version, installs: r.downloads ?? 0, status: r.status,
      created_at: r.created_at, updated_at: r.updated_at, published_at: r.published_at,
      publisher: r.publisher, publisher_discord_id: r.publisher_discord_id ?? null,
      publisher_fingerprint: r.publisher_fingerprint, publisher_verified: !!r.publisher_verified,
      permissions: r.permissions ? JSON.parse(r.permissions) : [],
      sdk_version: r.sdk_version ?? null,
      risk_score: r.risk_score ?? null,
      risk_report: r.risk_report ? safeJson(r.risk_report) : null,
    })),
  });
}

async function devSource(url: URL, req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const gate = await requireDeveloper(env, req);
  if (gate.resp) return gate.resp;
  const ns = (url.searchParams.get("namespace") || "").trim();
  const name = (url.searchParams.get("name") || "").trim();
  let version = (url.searchParams.get("version") || "").trim();
  if (!ns || !name) return json({ error: "namespace and name required" }, 400);
  if (!version) {
    const ext = await env.DB.prepare("SELECT latest_version FROM extensions WHERE namespace = ? AND name = ?")
      .bind(ns, name).first<{ latest_version: string }>();
    version = ext?.latest_version || "";
  }
  const row = await env.DB.prepare(
    "SELECT r2_key FROM versions WHERE namespace = ? AND name = ? AND version = ?",
  ).bind(ns, name, version).first<{ r2_key: string }>();
  if (!row) return json({ error: "not found" }, 404);
  const obj = await env.BUNDLES.get(row.r2_key);
  if (!obj) return json({ error: "bundle blob missing" }, 404);
  const bundle = await obj.json<any>();
  return json({ namespace: ns, name, version, source: bundle?.source ?? "", sdk_version: bundle?.sdk_version ?? null });
}

async function devReports(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const gate = await requireDeveloper(env, req);
  if (gate.resp) return gate.resp;
  const { results } = await env.DB.prepare(
    `SELECT id, namespace, name, version, publisher_discord_id, reporter_discord_id,
            description, logs_r2_key, status, created_at
     FROM reports ORDER BY created_at DESC LIMIT 500`,
  ).all();
  return json({ reports: results || [] });
}

async function devYank(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const gate = await requireDeveloper(env, req);
  if (gate.resp) return gate.resp;
  const body = await req.json<any>();
  const namespace = String(body?.namespace || "").trim();
  const name = String(body?.name || "").trim();
  const version = body?.version ? String(body.version) : null;
  if (!namespace || !name) return json({ error: "namespace and name required" }, 400);
  await yankExtension(env, namespace, name, version, "moderator");
  return json({ ok: true });
}

async function devBlocked(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const gate = await requireDeveloper(env, req);
  if (gate.resp) return gate.resp;
  const { results } = await env.DB.prepare(
    `SELECT id, namespace, name, version, reporter_discord_id, risk_score, threshold, bullets, created_at
     FROM blocked_publishes ORDER BY created_at DESC LIMIT 500`,
  ).all();
  return json({
    blocked: (results || []).map((b: any) => ({
      ...b,
      bullets: b.bullets ? safeJson(b.bullets) : [],
    })),
  });
}

async function devClearReports(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const gate = await requireDeveloper(env, req);
  if (gate.resp) return gate.resp;
  const r = await env.DB.prepare("DELETE FROM reports").run();
  return json({ ok: true, cleared: r.meta?.changes ?? 0 });
}

async function devClearBlocked(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const gate = await requireDeveloper(env, req);
  if (gate.resp) return gate.resp;
  const r = await env.DB.prepare("DELETE FROM blocked_publishes").run();
  return json({ ok: true, cleared: r.meta?.changes ?? 0 });
}

async function devModerationList(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const gate = await requireDeveloper(env, req);
  if (gate.resp) return gate.resp;
  const { results } = await env.DB.prepare(
    "SELECT discord_id, status, message, acknowledged, updated_at FROM moderation ORDER BY updated_at DESC",
  ).all();
  return json({ entries: results || [] });
}

async function devModeration(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const gate = await requireDeveloper(env, req);
  if (gate.resp) return gate.resp;
  const body = await req.json<any>();
  const discordId = String(body?.discord_id || "").trim();
  const action = String(body?.status || "").trim(); // warn | ban | clear
  const message = String(body?.message || "").slice(0, 500);
  if (!discordId) return json({ error: "discord_id required" }, 400);

  if (action === "clear") {
    await env.DB.prepare("DELETE FROM moderation WHERE discord_id = ?").bind(discordId).run();
    // Restore extensions de-listed by the ban (leave genuinely-yanked ones alone).
    await env.DB.prepare(
      `UPDATE extensions SET status = 'published'
       WHERE status = 'banned' AND publisher_id IN (SELECT id FROM publishers WHERE discord_id = ?)`,
    ).bind(discordId).run();
    return json({ ok: true, status: "ok" });
  }
  const status = action === "ban" ? "banned" : "warned";
  await env.DB.prepare(
    `INSERT INTO moderation (discord_id, status, message, acknowledged, updated_at)
     VALUES (?, ?, ?, 0, datetime('now'))
     ON CONFLICT(discord_id) DO UPDATE SET status = excluded.status, message = excluded.message,
       acknowledged = 0, updated_at = datetime('now')`,
  ).bind(discordId, status, message).run();
  if (status === "banned") {
    // De-list every listed extension by this publisher. A yanked one stays 'yanked', so
    // lifting the ban (which restores only 'banned' rows) can't bring it back.
    await env.DB.prepare(
      `UPDATE extensions SET status = 'banned'
       WHERE status = 'published' AND publisher_id IN (SELECT id FROM publishers WHERE discord_id = ?)`,
    ).bind(discordId).run();
  }
  return json({ ok: true, status });
}
