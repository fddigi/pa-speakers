"""F7: per-kilde kadence. Alle kilder deler ét launchd-interval (og kan nu
også trigges på tit ("Kør nu", trigger_watcher.py) - se main.py) -- men
Kleinanzeigen/Blocket øger blokeringsrisiko ved for hyppig hentning, mens
Reverb/Thomann/DBA tåler det fint. Denne fil lader ÉT hyppigt schema give
differentieret kadence pr. kilde uden flere launchd-jobs.

Rent lokal bogføring (last_run pr. kilde) -- synkroniseres bevidst IKKE til
Turso, det er kun operationel bogføring for denne ene maskines scraper-
instans, ikke brugervendt data.
"""
from __future__ import annotations

import datetime
import logging

logger = logging.getLogger(__name__)

SOURCE_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_state (
    source TEXT PRIMARY KEY,
    last_run_at TEXT NOT NULL
);
"""


def should_run_source(
    conn, source_name: str, min_interval_hours: dict, force: bool = False
) -> bool:
    """True hvis kilden bør køre nu. `force=True` (fra --source <navn>)
    ignorerer altid min-interval -- manuel fejlsøgning skal altid kunne køre."""
    if force:
        return True

    min_hours = min_interval_hours.get(source_name)
    if min_hours is None:
        return True  # ingen min-interval konfigureret for denne kilde

    row = conn.execute(
        "SELECT last_run_at FROM source_state WHERE source = ?", (source_name,)
    ).fetchone()
    if row is None:
        return True  # aldrig kørt før

    last_run = datetime.datetime.fromisoformat(row[0])
    elapsed_hours = (datetime.datetime.now(datetime.UTC) - last_run).total_seconds() / 3600
    if elapsed_hours < min_hours:
        logger.info(
            "%s: sprunget over - kørte for %.1f time(r) siden (min. interval: %dt)",
            source_name, elapsed_hours, min_hours,
        )
        return False
    return True


def mark_source_run(conn, source_name: str) -> None:
    now = datetime.datetime.now(datetime.UTC).isoformat()
    conn.execute(
        "INSERT INTO source_state (source, last_run_at) VALUES (?, ?) "
        "ON CONFLICT(source) DO UPDATE SET last_run_at = excluded.last_run_at",
        (source_name, now),
    )
    conn.commit()
