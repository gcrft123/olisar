-- For a registry database created from schema.sql before immutable versions and sticky
-- yanks. A new database needs none of this (schema.sql has it all).
-- Apply once, before deploying the Worker that uses it:
--   npx wrangler d1 execute olisar-registry --remote --file=./migrations/0001_immutable_versions_and_quotas.sql
-- The Worker adds the same column itself if it's missing, so if it was deployed
-- first, the ALTER below fails with "duplicate column name" and there's nothing
-- left to do.

-- Who yanked a whole extension: 'publisher' or 'moderator'. Existing yanks stay NULL, which
-- counts as a moderator's (a publisher can't bring one back by publishing).
ALTER TABLE extensions ADD COLUMN yanked_by TEXT;
