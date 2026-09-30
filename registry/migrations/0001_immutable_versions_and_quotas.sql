-- For a registry database created from schema.sql before immutable versions, sticky yanks
-- and per-publisher quotas. A new database needs none of this (schema.sql has it all).
-- Apply once, before deploying the Worker that uses it:
--   npx wrangler d1 execute olisar-registry --remote --file=./migrations/0001_immutable_versions_and_quotas.sql
-- The Worker adds the same table and column itself if they're missing, so if it was
-- deployed first, the ALTER below fails with "duplicate column name" and there's nothing
-- left to do.

-- Each publisher's share of the bucket: bytes of their bundles, and new versions per UTC day.
CREATE TABLE IF NOT EXISTS publisher_usage (
  publisher_id INTEGER PRIMARY KEY,
  stored_bytes INTEGER NOT NULL DEFAULT 0,
  publishes    INTEGER NOT NULL DEFAULT 0,
  period       TEXT NOT NULL DEFAULT ''
);

-- Who yanked a whole extension: 'publisher' or 'moderator'. Existing yanks stay NULL, which
-- counts as a moderator's (a publisher can't bring one back by publishing).
ALTER TABLE extensions ADD COLUMN yanked_by TEXT;
