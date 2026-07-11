#!/usr/bin/env bash
# Reusable Turso Platform API helpers. Sourced (never executed directly) by
# provision.sh, and reusable by add-user.sh if/when a project ever needs
# per-tenant databases (not used in the v1 --secret-mode / --table-mode flows,
# which share the one project database - see infra/add-user.sh).
#
# This talks ONLY to Turso's *management* API (create/delete database, mint
# tokens). Actual row-level libsql traffic always goes through the official
# libsql-client SDKs (packages/scraper-core/scraper_core/turso_client.py and
# worker/src/db.ts) - never hand-rolled HTTP against /v2/pipeline.
#
# Requires env: TURSO_PLATFORM_TOKEN, TURSO_ORG. Requires: curl, jq.

TURSO_API_BASE="${TURSO_API_BASE:-https://api.turso.tech}"

turso_api() {
  # turso_api <method> <path> [json-body]
  local method="$1" path="$2" body="${3:-}"
  local curl_args=(-sS -X "$method" "${TURSO_API_BASE}${path}"
    -H "Authorization: Bearer ${TURSO_PLATFORM_TOKEN}"
    -H "Content-Type: application/json")
  if [[ -n "$body" ]]; then
    curl_args+=(-d "$body")
  fi
  curl "${curl_args[@]}"
}

turso_database_exists() {
  # turso_database_exists <db-name>  -> exit 0 if it exists, 1 otherwise
  local db_name="$1"
  local resp
  resp=$(turso_api GET "/v1/organizations/${TURSO_ORG}/databases/${db_name}" 2>/dev/null || true)
  [[ "$(echo "$resp" | jq -r '.database.Name // empty' 2>/dev/null)" == "$db_name" ]]
}

turso_create_database() {
  # turso_create_database <db-name> [group]  -- idempotent: caller should check
  # turso_database_exists first (kept as a separate check, not hidden in here,
  # so callers can log "already exists, skipping" themselves).
  local db_name="$1" group="${2:-default}"
  log_info "Creating Turso database '${db_name}' (group=${group})..."
  turso_api POST "/v1/organizations/${TURSO_ORG}/databases" \
    "{\"name\": \"${db_name}\", \"group\": \"${group}\"}"
}

turso_get_hostname() {
  # turso_get_hostname <db-name> -> prints the db hostname (for libsql:// URLs)
  local db_name="$1"
  turso_api GET "/v1/organizations/${TURSO_ORG}/databases/${db_name}" \
    | jq -r '.database.Hostname'
}

turso_create_db_token() {
  # turso_create_db_token <db-name> [expiration] -> prints a scoped auth token
  local db_name="$1" expiration="${2:-never}"
  turso_api POST "/v1/organizations/${TURSO_ORG}/databases/${db_name}/auth/tokens?expiration=${expiration}" '{}' \
    | jq -r '.jwt'
}

turso_delete_database() {
  local db_name="$1"
  log_info "Deleting Turso database '${db_name}'..."
  turso_api DELETE "/v1/organizations/${TURSO_ORG}/databases/${db_name}"
}
