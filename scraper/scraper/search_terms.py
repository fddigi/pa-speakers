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

from .schema_utils import add_column_if_missing

logger = logging.getLogger(__name__)

# F9 v1: ren organisering/tagging, ikke et reelt synth/studie-scope -- se
# FEATURES.md F9's "Ærlig vurdering". Alle eksisterende og nyoprettede termer
# der ikke selv angiver en kategori, falder tilbage til denne.
DEFAULT_CATEGORY = "PA-højttalere"

SEARCH_TERMS_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_terms (
    term TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
"""


def _static_terms_from_config(rcf_config: dict) -> list[tuple[str, str]]:
    st = rcf_config.get("search_terms", {})
    terms = list(st.get("primary", [])) + list(st.get("secondary", []))
    return [(term, DEFAULT_CATEGORY) for term in terms]


def load_search_terms(rcf_config: dict, turso: TursoClient | None) -> list[tuple[str, str]]:
    """Returns the active (term, category) pairs.

    Without Turso: always the static config.yaml list (local-only mode),
    every term tagged with DEFAULT_CATEGORY (config.yaml doesn't carry
    categories in v1 - see FEATURES.md F9).

    With Turso: the `search_terms` table is the source of truth. On first use
    (empty table), it's seeded from config.yaml's existing list so a project's
    already-configured terms aren't silently lost the moment Turso is enabled -
    from then on, edits happen exclusively via the webapp.
    """
    if turso is None:
        return _static_terms_from_config(rcf_config)

    turso.execute(SEARCH_TERMS_SCHEMA)
    # F9: additive migration for an ALREADY-EXISTING search_terms table (this
    # table predates the category column) - see schema_utils.py.
    add_column_if_missing(
        turso, "search_terms", "category", f"TEXT NOT NULL DEFAULT '{DEFAULT_CATEGORY}'"
    )
    result = turso.execute("SELECT term, category FROM search_terms WHERE enabled = 1")
    if result.rows:
        return [(row[0], row[1]) for row in result.rows]

    static_terms = _static_terms_from_config(rcf_config)
    if static_terms:
        now = datetime.datetime.now(datetime.UTC).isoformat()
        turso.batch(
            [
                (
                    "INSERT INTO search_terms (term, category, enabled, created_at) "
                    "VALUES (?, ?, 1, ?) ON CONFLICT(term) DO NOTHING",
                    (term, category, now),
                )
                for term, category in static_terms
            ]
        )
        logger.info(
            "search_terms: seeded %d term(s) from config.yaml into Turso", len(static_terms)
        )
    return static_terms
