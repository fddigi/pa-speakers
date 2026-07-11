// Auth middleware: verifies the signed session cookie on every route it's applied
// to, otherwise responds 401. Applied to every route except POST /login.

import { getCookie } from "hono/cookie";
import type { Context, Next } from "hono";
import { verifySessionToken } from "./auth";
import type { Env, Variables } from "./types";

export const SESSION_COOKIE_NAME = "session";

export async function requireAuth(
  c: Context<{ Bindings: Env; Variables: Variables }>,
  next: Next,
) {
  const token = getCookie(c, SESSION_COOKIE_NAME);
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
