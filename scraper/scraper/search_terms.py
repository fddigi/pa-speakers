"""Dynamic search terms ("ønskeseddel"), inspired by PLAGG's wishlist.py: the
list of things to search for lives in Turso when configured, editable from the
webapp, instead of requiring a config.yaml edit + redeploy for every change.

Falls back to config.yaml's static search_terms.primary/secondary lists when
Turso isn't configured (local-only mode) - same "local file vs. hosted
webapp/database" split PLAGG uses, minus the Google Sheet option (never
needed here).
"""
from __future__ import annotations

import datetime
import logging

from scraper_core.turso_client import TursoClient

logger = logging.getLogger(__name__)

SEARCH_TERMS_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_terms (
    term TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
"""


def _static_terms_from_config(rcf_config: dict) -> list[str]:
    st = rcf_config.get("search_terms", {})
    return list(st.get("primary", [])) + list(st.get("secondary", []))


def load_search_terms(rcf_config: dict, turso: TursoClient | None) -> list[str]:
    """Returns the active search term list.

    Without Turso: always the static config.yaml list (local-only mode).

    With Turso: the `search_terms` table is the source of truth. On first use
    (empty table), it's seeded from config.yaml's existing list so a project's
    already-configured terms aren't silently lost the moment Turso is enabled -
    from then on, edits happen exclusively via the webapp.
    """
    if turso is None:
        return _static_terms_from_config(rcf_config)

    turso.execute(SEARCH_TERMS_SCHEMA)
    result = turso.execute("SELECT term FROM search_terms WHERE enabled = 1")
    if result.rows:
        return [row[0] for row in result.rows]

    static_terms = _static_terms_from_config(rcf_config)
    if static_terms:
        now = datetime.datetime.now(datetime.UTC).isoformat()
        turso.batch(
            [
                (
                    "INSERT INTO search_terms (term, enabled, created_at) VALUES (?, 1, ?) "
                    "ON CONFLICT(term) DO NOTHING",
                    (term, now),
                )
                for term in static_terms
            ]
        )
        logger.info(
            "search_terms: seeded %d term(s) from config.yaml into Turso", len(static_terms)
        )
    return static_terms
