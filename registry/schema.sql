-- Olisar extension registry — D1 schema.
-- Catalog metadata only; the .olx bundle blobs live in R2 (one object per namespace/name/version).
-- An existing database gets later changes from migrations/ (see README.md).

CREATE TABLE IF NOT EXISTS publishers (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  discord_id   TEXT,                     -- set only by /v1/publishers/verify (Discord OAuth)
  handle       TEXT NOT NULL,
  public_key   TEXT NOT NULL,            -- Ed25519 public key (base64)
  fingerprint  TEXT NOT NULL UNIQUE,     -- "sha256:<hex>" of the public key
  verified     INTEGER NOT NULL DEFAULT 0,  -- 1 once discord_id came from Discord OAuth
  token_hash   TEXT,                     -- sha256 of the publisher's bearer token
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS extensions (
  namespace      TEXT NOT NULL,          -- publisher handle / owner
  name           TEXT NOT NULL,          -- the bundle id
  publisher_id   INTEGER REFERENCES publishers(id),
  category       TEXT,
  description    TEXT,
  latest_version TEXT,
  downloads      INTEGER NOT NULL DEFAULT 0,
  status         TEXT NOT NULL DEFAULT 'published',  -- published | yanked | banned
  yanked_by      TEXT,                   -- publisher | moderator (NULL: not yanked, or yanked before this was recorded)
  created_at     TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (namespace, name)
);

CREATE TABLE IF NOT EXISTS versions (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  namespace     TEXT NOT NULL,
  name          TEXT NOT NULL,
  version       TEXT NOT NULL,
  content_hash  TEXT NOT NULL,           -- "sha256:<hex>" of canonical source (computed by the registry)
  r2_key        TEXT NOT NULL,           -- where the .olx blob lives in R2 (bundles/<ns>/<name>/<version>.olx)
  sdk_version   TEXT,
  permissions   TEXT,                    -- JSON array (declared/requested)
  signature     TEXT,                    -- publisher signature over content_hash
  publisher_key TEXT,                    -- signer public key (base64)
  risk_score    INTEGER,                 -- 0-100 AI risk assessment at publish (NULL = none)
  risk_report   TEXT,                    -- JSON array of short risk bullets
  yanked        INTEGER NOT NULL DEFAULT 0,  -- once 1, stays 1; a published row never otherwise changes
  published_at  TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (namespace, name, version)
);

-- Single-use nonces for /v1/publishers/register. A publisher proves it holds its key by
-- signing "olisar-registry/register:<nonce>:<handle>"; the nonce is deleted on first use
-- and expired rows are pruned whenever a new challenge is issued.
CREATE TABLE IF NOT EXISTS publisher_challenges (
  nonce      TEXT PRIMARY KEY,           -- 64 hex chars (32 random bytes)
  expires_at INTEGER NOT NULL            -- unix seconds
);

CREATE INDEX IF NOT EXISTS idx_publisher_challenges_expiry ON publisher_challenges (expires_at);

-- Discord verifications in progress: one row per sign-in link /v1/publishers/verify/start
-- hands out. The callback fills in who signed in and the hash of a cookie it gives that
-- browser; /v1/publishers/verify/confirm needs the cookie, then deletes the row.
CREATE TABLE IF NOT EXISTS publisher_verifications (
  state        TEXT PRIMARY KEY,         -- 64 hex chars, the OAuth state
  publisher_id INTEGER NOT NULL,
  expires_at   INTEGER NOT NULL,         -- unix seconds
  discord_id   TEXT,                     -- set by the callback
  username     TEXT,
  confirm_hash TEXT                      -- sha256 of the confirm cookie
);

CREATE INDEX IF NOT EXISTS idx_extensions_name ON extensions (name);
CREATE INDEX IF NOT EXISTS idx_extensions_category ON extensions (category);
CREATE INDEX IF NOT EXISTS idx_versions_ext ON versions (namespace, name);

-- Usage accounting so R2 can never exceed the free tier. id=1 is the whole bucket; id=2 is
-- the part report attachments use, capped separately so reports can't crowd out publishing.
-- stored_bytes is exact (reserved before every R2 write); class_a counts writes per month
-- (R2 reads/Class B stay under the free tier via the Workers free-plan request cap).
CREATE TABLE IF NOT EXISTS usage (
  id           INTEGER PRIMARY KEY,
  stored_bytes INTEGER NOT NULL DEFAULT 0,
  class_a      INTEGER NOT NULL DEFAULT 0,  -- R2 writes this period
  period       TEXT NOT NULL DEFAULT ''     -- YYYY-MM (resets class_a)
);

-- Each publisher's share of the bucket: bytes of their bundles, and new versions per UTC day.
CREATE TABLE IF NOT EXISTS publisher_usage (
  publisher_id INTEGER PRIMARY KEY,
  stored_bytes INTEGER NOT NULL DEFAULT 0,
  publishes    INTEGER NOT NULL DEFAULT 0,  -- new versions this period
  period       TEXT NOT NULL DEFAULT ''     -- YYYY-MM-DD (resets publishes)
);

-- Platform-owner developer whitelist (by Discord id). A token whose publisher is
-- verified (verified = 1) and whose discord_id is listed here may use the /v1/dev/*
-- moderation + management routes.
CREATE TABLE IF NOT EXISTS developers (
  discord_id TEXT PRIMARY KEY,
  note       TEXT,
  added_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Global moderation standing for a Discord id. status: 'warned' | 'banned'.
-- A warning shows once in the console (acknowledged flips to 1); a ban blocks
-- publishing, de-lists the publisher's extensions, and locks console + bot.
CREATE TABLE IF NOT EXISTS moderation (
  discord_id   TEXT PRIMARY KEY,
  status       TEXT NOT NULL,
  message      TEXT,
  acknowledged INTEGER NOT NULL DEFAULT 0,
  updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Abuse reports filed against a marketplace extension. Logs + attachments live in
-- R2 (keys below); the row keeps the searchable metadata + reporter/publisher ids.
CREATE TABLE IF NOT EXISTS reports (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  namespace            TEXT NOT NULL,
  name                 TEXT NOT NULL,
  version              TEXT,
  publisher_id         INTEGER,
  publisher_discord_id TEXT,
  reporter_discord_id  TEXT,
  description          TEXT,
  logs_r2_key          TEXT,
  attachments_r2_key   TEXT,
  status               TEXT NOT NULL DEFAULT 'open',
  created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_reports_ext ON reports (namespace, name);

-- Publishes blocked by a bot's AI risk review (recorded for the developer console).
CREATE TABLE IF NOT EXISTS blocked_publishes (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  namespace           TEXT,
  name                TEXT NOT NULL,
  version             TEXT,
  reporter_discord_id TEXT,
  risk_score          INTEGER,
  threshold           INTEGER,
  bullets             TEXT,                    -- JSON array of the flagged reasons
  created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
