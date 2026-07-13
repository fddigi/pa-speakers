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

-- F6: "blandet par"-alarm. Fuldt genberegnet af scraper/scraper/pairs.py ved
-- hver scraper-køring (ikke akkumulerende) - se worker/src/index.ts's
-- /api/mixed-pairs endpoint.
CREATE TABLE IF NOT EXISTS mixed_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL,
    gen TEXT NOT NULL,
    item_key_1 TEXT NOT NULL,
    item_key_2 TEXT NOT NULL,
    title_1 TEXT,
    title_2 TEXT,
    url_1 TEXT,
    url_2 TEXT,
    source_1 TEXT,
    source_2 TEXT,
    combined_price_dkk REAL NOT NULL,
    target_price_dkk REAL NOT NULL,
    distance_to_target_dkk REAL NOT NULL,
    computed_at TEXT NOT NULL
);

-- F11-spike: Thomann nypris-reference (display-anker, ikke klassifikations-
-- input). Upsert pr. model_key af scraper/scraper/thomann_new_price.py - se
-- worker/src/index.ts's /api/thomann-new-price endpoint.
CREATE TABLE IF NOT EXISTS thomann_new_price_ref (
    model_key TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    price_eur REAL NOT NULL,
    price_dkk REAL NOT NULL,
    checked_at TEXT NOT NULL
);

-- F5: prisfalds-detektion. Rent append (aldrig slettet/overskrevet) af
-- scraper/scraper/pipeline.py+price_history.py, hver gang en allerede kendt
-- annonces price_per_unit_dkk falder. Indlejres i GET /api/listings som
-- latest_price_drop_pct/latest_price_drop_at (korreleret subquery).
CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_key TEXT NOT NULL,
    old_price_per_unit_dkk REAL NOT NULL,
    new_price_per_unit_dkk REAL NOT NULL,
    pct_change REAL NOT NULL,
    old_classification TEXT,
    new_classification TEXT,
    observed_at TEXT NOT NULL
);
