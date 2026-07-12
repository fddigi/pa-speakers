"""Entry point for the PA-speakers scraper (RCF ART / Yamaha DXR).

Migrated from the original PA SPEAKERS project's monitor.py: same five sources,
same per-source try/except isolation, same normalize/classify business logic
(ported unchanged) -- now connected to scraper-core's delta-sync pattern instead
of a direct local-only SQLite write.

Run directly with `python -m scraper.main`, via the `scraper-run` console script,
or through the launchd job installed by `make install-launchd`.
"""

from __future__ import annotations

import logging
import sys

from scraper_core.config import get_settings
from scraper_core.healthcheck import ping_fail, ping_success
from scraper_core.local_db import LocalStore
from scraper_core.logging_setup import configure_logging
from scraper_core.sync import sync_pending
from scraper_core.turso_client import TursoClient

from .pipeline import TURSO_SCHEMA, run_source
from .rcf_config import load_config
from .search_terms import load_search_terms
from .sources import blocket, dba, kleinanzeigen, reverb, thomann

logger = logging.getLogger(__name__)

SOURCE_MODULES = {
    "reverb": reverb,
    "thomann": thomann,
    "kleinanzeigen": kleinanzeigen,
    "blocket": blocket,
    "dba": dba,
}


def run() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    rcf_config = load_config()

    try:
        with LocalStore(settings.local_sqlite_path) as store:
            total_raw = 0
            total_changed = 0

            enabled_sources = [
                name for name, enabled in rcf_config.get("sources", {}).items() if enabled
            ]

            if settings.turso_configured:
                with TursoClient(settings) as turso:
                    turso.execute(TURSO_SCHEMA)  # idempotent schema migration, not a data rewrite

                    # Dynamic search terms ("ønskeseddel"): Turso is the source of
                    # truth when configured, editable from the webapp - see
                    # search_terms.py. Overwrites config.yaml's static list in-memory
                    # only, so every source module's existing
                    # `config["search_terms"]["primary"] + secondary` read keeps
                    # working unchanged.
                    dynamic_terms = load_search_terms(rcf_config, turso)
                    rcf_config["search_terms"] = {"primary": dynamic_terms, "secondary": []}

                    for name in enabled_sources:
                        module = SOURCE_MODULES.get(name)
                        if module is None:
                            logger.warning("Unknown source configured: %s, skipping", name)
                            continue
                        raw_count, changed = run_source(store, name, module.fetch, rcf_config)
                        total_raw += raw_count
                        total_changed += changed

                    synced = sync_pending(store, turso)
                logger.info(
                    "run complete: %d raw across %d source(s), %d new/changed, %d synced to Turso",
                    total_raw, len(enabled_sources), total_changed, synced,
                )
            else:
                # Graceful fallback: no Turso credentials configured -> local-only mode.
                # The scraper still runs fully (all five sources, dedup, classification)
                # without any cloud account - see scraper-core's README. Search terms
                # come from config.yaml's static list only (no dynamic ønskeseddel
                # without Turso to store it in).
                for name in enabled_sources:
                    module = SOURCE_MODULES.get(name)
                    if module is None:
                        logger.warning("Unknown source configured: %s, skipping", name)
                        continue
                    raw_count, changed = run_source(store, name, module.fetch, rcf_config)
                    total_raw += raw_count
                    total_changed += changed

                logger.warning(
                    "TURSO_DATABASE_URL/TURSO_AUTH_TOKEN not set - skipping Turso sync "
                    "(local-only mode). %d new/changed item(s) queued locally.",
                    total_changed,
                )
    except Exception:
        logger.exception("scrape run failed")
        ping_fail(settings.healthcheck_url)
        return 1

    ping_success(settings.healthcheck_url)
    return 0


if __name__ == "__main__":
    sys.exit(run())
