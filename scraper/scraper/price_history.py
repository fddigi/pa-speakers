"""F5: Prisfalds-detektion. Append-only log af faktiske prisfald på allerede
kendte annoncer -- dedup-nøglen er source+url (item_key), så en annonce der
sætter prisen ned overskriver bare sin egen række i `listings` (allerede
understøttet af scraper-core's upsert_if_changed + pipeline.py's ON CONFLICT
DO UPDATE) uden at det ellers ville blive synligt som en HÆNDELSE.

Denne tabel er ren observation af det pipeline.py allerede opdager (samme sted
den ville have overskrevet den gamle pris uden at nogen så det) -- ingen
separat genbesøgs-crawler, ingen ændring af hvilke annoncer der hentes.
"""
from __future__ import annotations

PRICE_HISTORY_SCHEMA = """
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
"""


def sync_price_history_to_turso(turso, events: list[dict]) -> None:
    """Rent append -- modsat mixed_pairs er dette ALDRIG en fuld genberegning,
    hver række er en historisk hændelse der aldrig skal slettes eller
    overskrives."""
    turso.execute(PRICE_HISTORY_SCHEMA)
    if not events:
        return
    turso.batch(
        [
            (
                "INSERT INTO price_history (item_key, old_price_per_unit_dkk, "
                "new_price_per_unit_dkk, pct_change, old_classification, "
                "new_classification, observed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    e["item_key"], e["old_price_per_unit_dkk"], e["new_price_per_unit_dkk"],
                    e["pct_change"], e["old_classification"], e["new_classification"],
                    e["observed_at"],
                ),
            )
            for e in events
        ]
    )
