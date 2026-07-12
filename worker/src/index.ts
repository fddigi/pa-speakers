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
    allowMethods: ["GET", "POST", "OPTIONS"],
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

export default app;
