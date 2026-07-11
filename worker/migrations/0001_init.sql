-- NB (found during PA SPEAKERS migration, 2026-07-11): despite this header's claim,
-- infra/provision.sh does NOT actually apply this file anywhere - verified by reading
-- the full script. Neither `users` nor the data table below get created
-- automatically by any script in this template. `users` is applied manually once
-- (see migration report); the `listings` table below is instead self-applied
-- idempotently by the scraper itself on every run (scraper/scraper/main.py calling
-- `turso.execute(TURSO_SCHEMA)`), mirroring the dummy jsonplaceholder.py pattern -
-- so its presence here is for documentation/consistency, not because anything reads
-- this file mechanically.
--
-- v1 runs in "secret-mode" (see infra/add-user.sh --secret-mode) and does not
-- read from the `users` table at all - it exists from the start so a project can
-- be upgraded to --table-mode later without any API rewrite.

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    role TEXT NOT NULL DEFAULT 'admin'
);

-- RCF ART / Yamaha DXR PA-speaker listings, matches scraper/scraper/sources/*.py
-- and worker/src/index.ts's /api/listings endpoint. Migrated from PA SPEAKERS'
-- original db.py schema (source+url dedup, landed-cost pricing, dynamic
-- classification) - see that project's README.md for the full field semantics.
CREATE TABLE IF NOT EXISTS listings (
    item_key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT,
    model TEXT,
    gen TEXT,
    quantity INTEGER,
    price_dkk REAL,
    landed_price_dkk REAL,
    shipping_customs_dkk REAL,
    origin_country TEXT,
    price_per_unit_dkk REAL,
    classification TEXT,
    classification_method TEXT,
    url TEXT,
    first_seen TEXT NOT NULL,
    raw_json TEXT
);
