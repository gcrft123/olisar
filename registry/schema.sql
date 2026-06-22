-- Olisar extension registry — D1 schema.
-- Catalog metadata only; the .olx bundle blobs live in R2 (keyed by content_hash).

CREATE TABLE IF NOT EXISTS publishers (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  discord_id   TEXT,
  handle       TEXT NOT NULL,
  public_key   TEXT NOT NULL,            -- Ed25519 public key (base64)
  fingerprint  TEXT NOT NULL UNIQUE,     -- "sha256:<hex>" of the public key
  verified     INTEGER NOT NULL DEFAULT 0,  -- Discord-verified publisher (later)
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
  status         TEXT NOT NULL DEFAULT 'published',  -- published | yanked
  created_at     TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (namespace, name)
);

CREATE TABLE IF NOT EXISTS versions (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  namespace     TEXT NOT NULL,
  name          TEXT NOT NULL,
  version       TEXT NOT NULL,
  content_hash  TEXT NOT NULL,           -- "sha256:<hex>" of canonical source
  r2_key        TEXT NOT NULL,           -- where the .olx blob lives in R2
  sdk_version   TEXT,
  permissions   TEXT,                    -- JSON array (declared/requested)
  signature     TEXT,                    -- publisher signature over content_hash
  publisher_key TEXT,                    -- signer public key (base64)
  yanked        INTEGER NOT NULL DEFAULT 0,
  published_at  TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (namespace, name, version)
);

CREATE INDEX IF NOT EXISTS idx_extensions_name ON extensions (name);
CREATE INDEX IF NOT EXISTS idx_extensions_category ON extensions (category);
CREATE INDEX IF NOT EXISTS idx_versions_ext ON versions (namespace, name);

-- Usage accounting so R2 can never exceed the free tier. Single row (id=1).
-- stored_bytes is exact (checked on every publish); class_a counts writes per month
-- (R2 reads/Class B stay under the free tier via the Workers free-plan request cap).
CREATE TABLE IF NOT EXISTS usage (
  id           INTEGER PRIMARY KEY,
  stored_bytes INTEGER NOT NULL DEFAULT 0,
  class_a      INTEGER NOT NULL DEFAULT 0,  -- R2 writes this period
  period       TEXT NOT NULL DEFAULT ''     -- YYYY-MM (resets class_a)
);
