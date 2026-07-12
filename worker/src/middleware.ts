// Auth middleware: verifies the signed session token on every route it's applied
// to, otherwise responds 401. Applied to every route except POST /login.
//
// Reads the token from an `Authorization: Bearer <token>` header FIRST, falling
// back to the (legacy) session cookie. Bearer-header is the primary mechanism -
// see index.ts's /login handler comment for why: frontend and Worker are on two
// different registrable domains, and Safari's ITP (and increasingly Chrome)
// blocks cross-site cookies entirely by default, regardless of SameSite. A
// token in localStorage sent via a normal header isn't a cookie at all, so no
// browser cookie policy applies to it.

import { getCookie } from "hono/cookie";
import type { Context, Next } from "hono";
import { verifySessionToken } from "./auth";
import type { Env, Variables } from "./types";

export const SESSION_COOKIE_NAME = "session";

function extractToken(c: Context<{ Bindings: Env; Variables: Variables }>): string | undefined {
  const authHeader = c.req.header("Authorization");
  if (authHeader?.startsWith("Bearer ")) {
    return authHeader.slice("Bearer ".length);
  }
  return getCookie(c, SESSION_COOKIE_NAME);
}

export async function requireAuth(
  c: Context<{ Bindings: Env; Variables: Variables }>,
  next: Next,
) {
  const token = extractToken(c);
  if (!token) {
    return c.json({ error: "unauthorized" }, 401);
  }

  const payload = await verifySessionToken(token, c.env.SESSION_HMAC_SECRET);
  if (!payload) {
    return c.json({ error: "unauthorized" }, 401);
  }

  c.set("session", payload);
  await next();
}
