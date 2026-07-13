// API proxy Worker for this project's Turso database. One Worker per PROJECT,
// never one Worker per user. See README.md for the full auth model ("the padlock").

import { Hono } from "hono";
import { cors } from "hono/cors";
import { deleteCookie, setCookie } from "hono/cookie";

import { createSessionToken, verifyPassword } from "./auth";
import { getDbClient } from "./db";
import { requireAuth, SESSION_COOKIE_NAME } from "./middleware";
import { checkAndIncrementLoginAttempts } from "./rateLimit";
import type { Env, Variables } from "./types";

const app = new Hono<{ Bindings: Env; Variables: Variables }>();

// CORS locked to exactly one configurable Pages origin - never "*". `origin` needs
// env access, which in Hono is only available per-request, hence the wrapper.
app.use("*", async (c, next) => {
  const middleware = cors({
    origin: c.env.ALLOWED_ORIGIN,
    credentials: true,
    allowMethods: ["GET", "POST", "DELETE", "OPTIONS"],
    allowHeaders: ["Content-Type", "Authorization"],
  });
  return middleware(c, next);
});

app.get("/", (c) => c.json({ status: "ok" }));

app.post("/login", async (c) => {
  let body: { username?: string; password?: string };
  try {
    body = await c.req.json();
  } catch {
    body = {};
  }
  const { username, password } = body;
  if (!username || !password) {
    return c.json({ error: "username and password are required" }, 400);
  }

  const ip = c.req.header("CF-Connecting-IP") ?? "unknown";
  const { allowed } = await checkAndIncrementLoginAttempts(c.env.RATE_LIMIT_KV, ip, username);
  if (!allowed) {
    return c.json({ error: "too many login attempts - try again later" }, 429);
  }

  // v1 runs in "secret-mode": the one admin's credentials live as Worker secrets
  // (ADMIN_USER / ADMIN_PW_HASH), set by infra/add-user.sh --secret-mode. The
  // `users` table (see worker/migrations/0001_init.sql) already exists from v1
  // onwards so a project can move to --table-mode later by swapping this block
  // for a `SELECT * FROM users WHERE username = ?` lookup - no other API changes
  // needed, and a `user_id` FK can be threaded through the same way.
  if (username !== c.env.ADMIN_USER) {
    return c.json({ error: "invalid credentials" }, 401);
  }
  const valid = await verifyPassword(password, c.env.ADMIN_PW_HASH);
  if (!valid) {
    return c.json({ error: "invalid credentials" }, 401);
  }

  const maxAgeDays = Number(c.env.SESSION_COOKIE_MAX_AGE_DAYS ?? "30");
  const maxAgeSeconds = maxAgeDays * 24 * 60 * 60;
  const token = await createSessionToken(
    { sub: username, role: "admin", exp: Math.floor(Date.now() / 1000) + maxAgeSeconds },
    c.env.SESSION_HMAC_SECRET,
  );

  // Cookie kept as a fallback/legacy mechanism only - NOT the primary auth
  // transport. Confirmed 2026-07-12: even with sameSite:"None" (required
  // since frontend and Worker are different registrable domains), Safari's
  // ITP blocks cross-site cookies entirely by default (not just SameSite=
  // Strict/Lax - a blanket third-party-cookie block), and Chrome is moving
  // the same direction. The primary mechanism is now the `token` returned
  // below, stored in the frontend's localStorage and sent as a normal
  // `Authorization: Bearer` header - not a cookie, so no browser cookie
  // policy applies to it at all. See middleware.ts's requireAuth.
  setCookie(c, SESSION_COOKIE_NAME, token, {
    httpOnly: true,
    secure: true,
    sameSite: "None",
    path: "/",
    maxAge: maxAgeSeconds,
  });

  return c.json({ ok: true, username, role: "admin", token });
});

app.post("/logout", requireAuth, (c) => {
  deleteCookie(c, SESSION_COOKIE_NAME, { path: "/" });
  return c.json({ ok: true });
});

app.get("/api/me", requireAuth, (c) => {
  const session = c.get("session");
  return c.json({ username: session.sub, role: session.role });
});

// --- Dynamic search terms ("ønskeseddel"), inspired by PLAGG's own webapp-
// editable wishlist: this table is the scraper's source of truth for what to
// search for once Turso is configured (see scraper/scraper/search_terms.py) -
// editable here instead of requiring a config.yaml edit + redeploy. ---

app.get("/api/search-terms", requireAuth, async (c) => {
  const db = getDbClient(c.env);
  const result = await db.execute(
    "SELECT term, enabled, created_at FROM search_terms ORDER BY created_at DESC",
  );
  return c.json({ searchTerms: result.rows });
});

app.post("/api/search-terms", requireAuth, async (c) => {
  const body = await c.req.json().catch(() => ({}));
  const term = typeof body.term === "string" ? body.term.trim() : "";
  if (!term) {
    return c.json({ error: "term is required" }, 400);
  }
  const db = getDbClient(c.env);
  await db.execute({
    sql: `INSERT INTO search_terms (term, enabled, created_at) VALUES (?, 1, ?)
          ON CONFLICT(term) DO UPDATE SET enabled = 1`,
    args: [term, new Date().toISOString()],
  });
  return c.json({ ok: true, term });
});

app.delete("/api/search-terms/:term", requireAuth, async (c) => {
  const term = c.req.param("term");
  if (!term) {
    return c.json({ error: "term is required" }, 400);
  }
  const db = getDbClient(c.env);
  await db.execute({ sql: "DELETE FROM search_terms WHERE term = ?", args: [term] });
  return c.json({ ok: true });
});

// --- "Kør nu" (run-now trigger), inspired by PLAGG's own webapp trigger
// button: sets a flag in Turso's `control` singleton row, which
// scraper/scraper/trigger_watcher.py (a separate always-running launchd job,
// see make install-launchd-watcher) polls every 15s and acts on. This Worker
// never runs the scraper itself - it only flips a flag the local machine
// picks up, since a Cloudflare Worker cannot run Playwright/long-lived
// scraping jobs. ---

// Ensures the singleton control row exists even if this Worker is hit before
// trigger_watcher.py has ever run locally (that script also applies this
// same schema idempotently on its own startup - whichever runs first wins,
// no conflict either way).
async function ensureControlRow(db: ReturnType<typeof getDbClient>): Promise<void> {
  await db.execute(
    `CREATE TABLE IF NOT EXISTS control (
      id INTEGER PRIMARY KEY CHECK (id = 1),
      run_now INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'Klar',
      last_run_at TEXT,
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )`,
  );
  await db.execute("INSERT OR IGNORE INTO control (id) VALUES (1)");
}

app.get("/api/status", requireAuth, async (c) => {
  const db = getDbClient(c.env);
  await ensureControlRow(db);
  const result = await db.execute("SELECT * FROM control WHERE id = 1");
  return c.json(result.rows[0] ?? null);
});

app.post("/api/trigger", requireAuth, async (c) => {
  const db = getDbClient(c.env);
  await ensureControlRow(db);
  await db.execute(
    "UPDATE control SET run_now = 1, updated_at = datetime('now') WHERE id = 1",
  );
  return c.json({ ok: true });
});

// --- F6: "blandet par"-alarm. Fuldt genberegnet af scraper/scraper/pairs.py
// ved hver scraper-køring (ikke akkumulerende) - dette endpoint læser bare
// den nuværende bestand, sorteret bedste-først (mindste afstand til mål). ---
app.get("/api/mixed-pairs", requireAuth, async (c) => {
  const db = getDbClient(c.env);
  // Idempotent: harmless if the scraper has already created this (it will
  // have, after its first F6-aware run) - avoids a confusing "no such table"
  // error if this endpoint is hit before that.
  await db.execute(
    `CREATE TABLE IF NOT EXISTS mixed_pairs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      model TEXT NOT NULL, gen TEXT NOT NULL,
      item_key_1 TEXT NOT NULL, item_key_2 TEXT NOT NULL,
      title_1 TEXT, title_2 TEXT, url_1 TEXT, url_2 TEXT,
      source_1 TEXT, source_2 TEXT,
      combined_price_dkk REAL NOT NULL, target_price_dkk REAL NOT NULL,
      distance_to_target_dkk REAL NOT NULL, computed_at TEXT NOT NULL
    )`,
  );
  const result = await db.execute(
    "SELECT * FROM mixed_pairs ORDER BY distance_to_target_dkk ASC",
  );
  return c.json({ mixedPairs: result.rows });
});

// --- F11-spike: Thomann nypris-reference. Rent DISPLAY-ANKER ved siden af
// brugtannoncer (scraper/scraper/thomann_new_price.py), IKKE input til
// klassifikationen - se FEATURES.md F11. Upsert pr. model, ikke fuld
// genberegning som mixed_pairs, så en fejlet Thomann-hentning aldrig sletter
// en tidligere kendt reference. ---
app.get("/api/thomann-new-price", requireAuth, async (c) => {
  const db = getDbClient(c.env);
  await db.execute(
    `CREATE TABLE IF NOT EXISTS thomann_new_price_ref (
      model_key TEXT PRIMARY KEY,
      url TEXT NOT NULL, price_eur REAL NOT NULL, price_dkk REAL NOT NULL,
      checked_at TEXT NOT NULL
    )`,
  );
  const result = await db.execute("SELECT * FROM thomann_new_price_ref");
  return c.json({ newPriceRefs: result.rows });
});

// --- Data endpoints against the `listings` table (matches
// scraper/scraper/sources/*.py and worker/migrations/0001_init.sql). Migrated from
// the PA SPEAKERS project's read-only dashboard.html - this is display-only, so
// unlike the dummy `posts` example there is no POST /api/listings write endpoint:
// the scraper writes exclusively via scraper-core's delta-sync outbox, and no
// admin-panel manual-correction feature was requested for this project. ---

app.get("/api/listings", requireAuth, async (c) => {
  const db = getDbClient(c.env);
  const limit = Math.min(Number(c.req.query("limit") ?? "500") || 500, 2000);

  // Optional filters mirror dashboard.html's classification/model/source dropdowns.
  const classification = c.req.query("classification");
  const model = c.req.query("model");
  const source = c.req.query("source");

  const conditions: string[] = [];
  const args: (string | number)[] = [];
  if (classification) {
    conditions.push("classification = ?");
    args.push(classification);
  }
  if (model) {
    conditions.push("model = ?");
    args.push(model);
  }
  if (source) {
    conditions.push("source = ?");
    args.push(source);
  }
  const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
  args.push(limit);

  const result = await db.execute({
    sql: `SELECT * FROM listings ${where} ORDER BY first_seen DESC LIMIT ?`,
    args,
  });
  return c.json({ listings: result.rows });
});

app.get("/api/listings/:itemKey", requireAuth, async (c) => {
  const db = getDbClient(c.env);
  const itemKey = c.req.param("itemKey");
  if (!itemKey) {
    return c.json({ error: "itemKey is required" }, 400);
  }
  const result = await db.execute({
    sql: "SELECT * FROM listings WHERE item_key = ?",
    args: [itemKey],
  });
  if (result.rows.length === 0) {
    return c.json({ error: "not found" }, 404);
  }
  return c.json({ listing: result.rows[0] });
});

// --- F10-spike: klassifikations-drilldown. Konklusion (se FEATURES.md F10):
// vi kan IKKE vise en pris-over-tid-tidsserie (hver annonce er en unik
// engangsvare, ingen realiserede salgspriser observeres) - det eneste
// meningsfulde er FORDELINGEN af udbudspriser bag classify_dynamic()'s
// percentil-beregning i scraper/scraper/classify.py. Dette endpoint
// replikerer den samme percentil-forespørgsel (samme model+gen,
// lookback_days=180, min_samples=5) direkte mod Turso - option (a) fra
// spiken, ingen precomputed/gemt kontekst pr. annonce (undgår duplikeret
// lagring for noget der er billigt at genberegne ved læsning). ---
const CONTEXT_LOOKBACK_DAYS = 180;
const CONTEXT_MIN_SAMPLES = 5;

function percentile(sorted: number[], pct: number): number | null {
  if (sorted.length === 0) return null;
  const k = (sorted.length - 1) * (pct / 100);
  const f = Math.floor(k);
  const cIdx = Math.min(f + 1, sorted.length - 1);
  if (f === cIdx) return sorted[f];
  return sorted[f] + (sorted[cIdx] - sorted[f]) * (k - f);
}

app.get("/api/listings/:itemKey/context", requireAuth, async (c) => {
  const db = getDbClient(c.env);
  const itemKey = c.req.param("itemKey");
  if (!itemKey) {
    return c.json({ error: "itemKey is required" }, 400);
  }

  const listingResult = await db.execute({
    sql: "SELECT * FROM listings WHERE item_key = ?",
    args: [itemKey],
  });
  const listing = listingResult.rows[0];
  if (!listing) {
    return c.json({ error: "not found" }, 404);
  }

  const cutoff = new Date(
    Date.now() - CONTEXT_LOOKBACK_DAYS * 24 * 3600 * 1000,
  ).toISOString();

  const comparablesResult = await db.execute({
    sql: `SELECT item_key, title, source, url, price_per_unit_dkk, first_seen
          FROM listings
          WHERE model = ? AND gen = ? AND price_per_unit_dkk IS NOT NULL
          AND first_seen >= ? AND item_key != ?
          ORDER BY price_per_unit_dkk ASC`,
    args: [listing.model as string, listing.gen as string, cutoff, itemKey],
  });
  const comparables = comparablesResult.rows;
  const prices = comparables
    .map((r) => Number(r.price_per_unit_dkk))
    .sort((a, b) => a - b);

  const n = prices.length;
  const usesDynamic = n >= CONTEXT_MIN_SAMPLES;

  return c.json({
    itemKey,
    model: listing.model,
    gen: listing.gen,
    thisPriceDkk: listing.price_per_unit_dkk,
    classification: listing.classification,
    classificationMethod: listing.classification_method,
    lookbackDays: CONTEXT_LOOKBACK_DAYS,
    minSamples: CONTEXT_MIN_SAMPLES,
    n,
    method: usesDynamic
      ? `dynamisk (n=${n})`
      : `statisk (utilstrækkelig historik, n=${n} < ${CONTEXT_MIN_SAMPLES})`,
    p25: usesDynamic ? percentile(prices, 25) : null,
    p75: usesDynamic ? percentile(prices, 75) : null,
    comparables,
  });
});

export default app;
