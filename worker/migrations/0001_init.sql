-- NB (updated 2026-07-12): infra/provision.sh's provision_turso_database() now
-- DOES apply this file automatically, via Turso's HTTP pipeline API (see
-- turso_execute_sql_file() in infra/lib/turso.sh) - the earlier note here
-- claiming otherwise was fixed in the template. `listings` and `search_terms`
-- are ALSO self-applied idempotently by the scraper itself on every run
-- (scraper/scraper/main.py / search_terms.py calling turso.execute(...)), so
-- their presence here is for documentation/consistency as much as for the
-- one-time provisioning path.
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

-- Dynamic search terms ("ønskeseddel"), inspired by PLAGG's own webapp-editable
-- wishlist: the scraper's source of truth for what to search for once Turso is
-- configured, editable via the frontend instead of requiring a config.yaml edit
-- + redeploy. See scraper/scraper/search_terms.py and worker/src/index.ts's
-- /api/search-terms endpoints.
CREATE TABLE IF NOT EXISTS search_terms (
    term TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

-- "Kør nu"-kontrol (single-row), inspireret af PLAGG's webapp-trigger: sat af
-- frontend'ens knap (POST /api/trigger), pollet af scraper/scraper/
-- trigger_watcher.py (en separat, altid-koerende launchd-job). Se
-- worker/src/index.ts's /api/status, /api/trigger endpoints.
CREATE TABLE IF NOT EXISTS control (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    run_now INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'Klar',
    last_run_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO control (id) VALUES (1);
