/**
 * Olisar extension marketplace registry.
 *
 * A Cloudflare Worker that hosts the catalog (D1) and the `.olx` bundle blobs (R2).
 * This phase is the read-only **consume** API the bot's console browses + installs from;
 * the bot always re-transpiles and verifies the bundle locally, so the registry is a
 * discovery + distribution layer, never a trusted compiler.
 *
 * Routes:
 *   GET  /v1/health
 *   GET  /v1/search?q=&category=&limit=&offset=
 *   GET  /v1/ext/:namespace/:name              → catalog detail + versions
 *   GET  /v1/ext/:namespace/:name/:version     → the .olx bundle (JSON, from R2)
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

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json", ...CORS },
  });
}

// R2 object key for a bundle, derived from its content hash (immutable + dedup).
function bundleKey(contentHash: string): string {
  return "bundles/" + contentHash.replace(/^sha256:/, "") + ".olx";
}

const HANDLE_RE = /^[a-z0-9_-]{2,64}$/;

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
    "SELECT id, handle, public_key, verified FROM publishers WHERE token_hash = ?",
  ).bind(await sha256hex(token)).first();
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
    publisher: r.publisher ?? null,
    publisher_fingerprint: r.publisher_fingerprint ?? null,
    publisher_verified: !!r.publisher_verified,
  };
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
      v.permissions AS permissions, v.sdk_version AS sdk_version
    FROM extensions e
    LEFT JOIN publishers p ON p.id = e.publisher_id
    LEFT JOIN versions v ON v.namespace = e.namespace AND v.name = e.name AND v.version = e.latest_version
    WHERE e.status = 'published'`;
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
    `SELECT e.*, p.handle AS publisher, p.fingerprint AS publisher_fingerprint, p.verified AS publisher_verified
     FROM extensions e LEFT JOIN publishers p ON p.id = e.publisher_id
     WHERE e.namespace = ? AND e.name = ?`,
  ).bind(ns, name).first<any>();
  if (!ext) return json({ error: "not found" }, 404);

  const { results: versions } = await env.DB.prepare(
    `SELECT version, content_hash, sdk_version, permissions, signature, publisher_key, yanked, published_at
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
    `SELECT content_hash, yanked FROM versions WHERE namespace = ? AND name = ? AND version = ?`,
  ).bind(ns, name, version).first<{ content_hash: string; yanked: number }>();
  if (!row) return json({ error: "not found" }, 404);

  const obj = await env.BUNDLES.get(bundleKey(row.content_hash));
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
  ]);
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
  const bundle = body?.bundle;
  if (!bundle || !bundle.id || !bundle.content_hash) {
    return json({ error: "bad bundle (need id + content_hash)" }, 400);
  }
  const pub = body.publisher || {};
  const namespace = String(body.namespace || pub.handle || "demo");
  return storePublish(env, namespace, pub, bundle);
}

async function storePublish(env: Env, namespace: string, pub: any, bundle: any): Promise<Response> {
  const name = String(bundle.id);
  const version = String(bundle.version || "1.0.0");
  const key = bundleKey(bundle.content_hash);
  const blob = JSON.stringify(bundle);
  const size = new TextEncoder().encode(blob).length;
  const lim = limits(env);
  if (size > lim.maxBundle) {
    return json({ error: `bundle too large (${size} > ${lim.maxBundle} bytes)` }, 413);
  }

  // Free-tier guard — enforce the exact storage + monthly-write caps before any R2 write.
  const period = new Date().toISOString().slice(0, 7); // YYYY-MM
  const u = await env.DB.prepare(`SELECT stored_bytes, class_a, period FROM usage WHERE id = 1`)
    .first<{ stored_bytes: number; class_a: number; period: string }>();
  let stored = u?.stored_bytes ?? 0;
  let classA = u && u.period === period ? (u.class_a ?? 0) : 0; // reset writes each month
  // Bundles are content-addressed, so re-publishing identical bytes adds no storage.
  const existing = await env.BUNDLES.head(key);
  const delta = existing ? 0 : size;
  if (stored + delta > lim.maxBytes) {
    return json({ error: "registry storage cap reached" }, 507);
  }
  if (classA + 1 > lim.maxClassA) {
    return json({ error: "registry monthly write cap reached" }, 429);
  }

  await env.BUNDLES.put(key, blob, { httpMetadata: { contentType: "application/json" } });
  stored += delta;
  classA += 1;
  await env.DB.prepare(
    `INSERT INTO usage (id, stored_bytes, class_a, period) VALUES (1, ?, ?, ?)
     ON CONFLICT(id) DO UPDATE SET stored_bytes = excluded.stored_bytes,
       class_a = excluded.class_a, period = excluded.period`,
  ).bind(stored, classA, period).run();

  let publisherId: number | null = null;
  if (pub.fingerprint) {
    await env.DB.prepare(
      `INSERT INTO publishers (discord_id, handle, public_key, fingerprint, verified)
       VALUES (?, ?, ?, ?, ?)
       ON CONFLICT(fingerprint) DO UPDATE SET handle = excluded.handle, verified = excluded.verified`,
    ).bind(pub.discord_id ?? null, pub.handle ?? namespace, pub.public_key ?? "", pub.fingerprint, pub.verified ? 1 : 0).run();
    const prow = await env.DB.prepare(`SELECT id FROM publishers WHERE fingerprint = ?`)
      .bind(pub.fingerprint).first<{ id: number }>();
    publisherId = prow ? prow.id : null;
  }

  await env.DB.prepare(
    `INSERT INTO extensions (namespace, name, publisher_id, category, description, latest_version, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
     ON CONFLICT(namespace, name) DO UPDATE SET
       publisher_id = excluded.publisher_id, category = excluded.category,
       description = excluded.description, latest_version = excluded.latest_version,
       updated_at = datetime('now')`,
  ).bind(namespace, name, publisherId, bundle.category ?? "General", bundle.description ?? "", version).run();

  await env.DB.prepare(
    `INSERT INTO versions (namespace, name, version, content_hash, r2_key, sdk_version, permissions, signature, publisher_key)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT(namespace, name, version) DO UPDATE SET
       content_hash = excluded.content_hash, r2_key = excluded.r2_key, sdk_version = excluded.sdk_version,
       permissions = excluded.permissions, signature = excluded.signature, publisher_key = excluded.publisher_key`,
  ).bind(
    namespace, name, version, bundle.content_hash, key,
    bundle.sdk_version ?? "1", JSON.stringify(bundle.permissions ?? []),
    bundle.signature ?? null, bundle.public_key ?? null,
  ).run();

  return json({ ok: true, id: `${namespace}/${name}`, version, stored_bytes: stored });
}

// ── self-serve publishing ──────────────────────────────────────────────────
// Register a publisher: a handle (namespace) bound to an Ed25519 public key, on a
// first-come basis (trust-on-first-use). Re-registering with the same key rotates the
// token. Discord-verified identity (the "verified" badge) is a later layer.
async function publishersRegister(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const body = await req.json<any>();
  const pub = String(body.public_key || "");
  const handle = String(body.handle || "").toLowerCase();
  if (!pub || !HANDLE_RE.test(handle)) {
    return json({ error: "a public_key and a handle (2-64 chars, [a-z0-9_-]) are required" }, 400);
  }
  const fp = await fingerprintOf(pub);
  const owner = await env.DB.prepare("SELECT fingerprint FROM publishers WHERE handle = ?")
    .bind(handle).first<{ fingerprint: string }>();
  if (owner && owner.fingerprint !== fp) {
    return json({ error: `the handle '${handle}' is already taken` }, 409);
  }
  const token = randomToken();
  await env.DB.prepare(
    `INSERT INTO publishers (discord_id, handle, public_key, fingerprint, verified, token_hash)
     VALUES (?, ?, ?, ?, 0, ?)
     ON CONFLICT(fingerprint) DO UPDATE SET handle = excluded.handle,
       token_hash = excluded.token_hash, discord_id = excluded.discord_id`,
  ).bind(body.discord_id ?? null, handle, pub, fp, await sha256hex(token)).run();
  return json({ ok: true, handle, fingerprint: fp, token });
}

// Publish a signed bundle under the authenticated publisher's namespace. The signature
// must be by the publisher's registered key — so a handle can only ship code its key
// owner signed (the installing bot independently re-verifies on download).
async function publisherPublish(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const publisher = await publisherForToken(env, req);
  if (!publisher) return json({ error: "unauthorized" }, 401);
  const body = await req.json<any>();
  const bundle = body?.bundle;
  if (!bundle || !bundle.id || !bundle.content_hash) {
    return json({ error: "bad bundle (need id + content_hash)" }, 400);
  }
  if (!bundle.public_key || bundle.public_key !== publisher.public_key) {
    return json({ error: "bundle is not signed by your publisher key" }, 403);
  }
  if (!bundle.signature || !(await verifyEd25519(publisher.public_key, bundle.content_hash, bundle.signature))) {
    return json({ error: "invalid bundle signature" }, 403);
  }
  const pub = {
    handle: publisher.handle, public_key: publisher.public_key,
    fingerprint: await fingerprintOf(publisher.public_key),
    verified: publisher.verified, discord_id: null,
  };
  return storePublish(env, publisher.handle, pub, bundle);
}

// Bind a verified Discord identity to the authenticated publisher. The bot forwards the
// operator's short-lived `identify` token; the registry confirms it with Discord itself
// (so it never just trusts the bot's word) and sets the verified badge.
async function publisherVerify(req: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const publisher = await publisherForToken(env, req);
  if (!publisher) return json({ error: "unauthorized" }, 401);
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
  if (version) {
    await env.DB.prepare("UPDATE versions SET yanked = 1 WHERE namespace = ? AND name = ? AND version = ?")
      .bind(publisher.handle, name, version).run();
  } else {
    await env.DB.prepare("UPDATE versions SET yanked = 1 WHERE namespace = ? AND name = ?")
      .bind(publisher.handle, name).run();
    await env.DB.prepare("UPDATE extensions SET status = 'yanked' WHERE namespace = ? AND name = ?")
      .bind(publisher.handle, name).run();
  }
  return json({ ok: true });
}
