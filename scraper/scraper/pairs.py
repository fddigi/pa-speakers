"""F6: "Blandet par"-alarm. De fleste annoncer er enkeltenheder, men målet er
et PAR (fx 910A <=6.500) -- to billige singler af samme model+generation kan
tilsammen opfylde parmålet, selvom ingen enkelt annonce gør det alene. Ren
efterbehandling af data vi allerede har: ingen ny datahentning.

Beregnes friskt ved hver scraper-køring (se main.py) og skrives til Turso's
`mixed_pairs`-tabel (fuldt genberegnet, ikke akkumulerende) -- Worker'en
læser den bare (GET /api/mixed-pairs), samme "Python ejer forretningslogik,
Worker er tyndt læse-lag"-mønster som resten af projektet.
"""
from __future__ import annotations

import datetime

from .classify import _threshold_key

MIXED_PAIRS_SCHEMA = """
CREATE TABLE IF NOT EXISTS mixed_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL,
    gen TEXT NOT NULL,
    item_key_1 TEXT NOT NULL,
    item_key_2 TEXT NOT NULL,
    title_1 TEXT,
    title_2 TEXT,
    url_1 TEXT,
    url_2 TEXT,
    source_1 TEXT,
    source_2 TEXT,
    combined_price_dkk REAL NOT NULL,
    target_price_dkk REAL NOT NULL,
    distance_to_target_dkk REAL NOT NULL,
    computed_at TEXT NOT NULL
);
"""


def _region_key(origin_country: str | None, eu_country_codes: set) -> str:
    """Grov region-nøgle til same_region_only-tjekket: alle EU-lande grupperes
    sammen (fragt inden for EU er sammenlignelig for vores formål), alt andet
    er sit eget land -- to singler fra forskellige ikke-EU-lande giver typisk
    dobbelt international fragt og parres derfor ikke."""
    if origin_country is None:
        return "UKENDT"
    origin_country = origin_country.upper()
    return "EU" if origin_country in eu_country_codes else origin_country


def _pair_threshold(model: str, gen: str, thresholds: dict, mk1_beater: dict) -> float | None:
    """Par-prisgrænsen (godt_koeb_max) for denne model+generation, eller None
    hvis der ikke findes en tærskel at måle imod (samme mapping som
    classify.py's statiske klassifikation, så par-alarmen og enkelt-
    klassifikationen altid er enige om hvad "godt køb" betyder)."""
    if model == "710a" and gen == "MK1":
        return mk1_beater.get("godt_koeb_max")
    key = _threshold_key(model, gen)
    if key is None or key not in thresholds:
        return None
    return thresholds[key]["godt_koeb_max"]


def compute_mixed_pairs(
    conn, thresholds: dict, mk1_beater: dict, mixed_pair_config: dict, eu_country_codes: set
) -> list[dict]:
    """Finder den bedste blandede par-mulighed pr. (model, gen): to aktive
    enkeltannoncer (quantity=1, klassifikation != UKENDT) hvis samlede pris
    rammer par-tærsklen, evt. med en near-miss-margin.

    "Aktiv" er svagt defineret (vi ved ikke om en annonce reelt er solgt) --
    false positives accepteres i v1, se F5 (last_seen) for en fremtidig
    stramning.

    Returnerer en liste af par-dicts, sorteret efter afstand til mål (bedste
    først, kan være negativ = under mål).
    """
    same_region_only = mixed_pair_config.get("same_region_only", True)
    near_miss_pct = mixed_pair_config.get("near_miss_pct", 15)

    rows = conn.execute(
        "SELECT item_key, model, gen, title, url, source, price_per_unit_dkk, "
        "origin_country FROM listings "
        "WHERE quantity = 1 AND classification != 'UKENDT' "
        "AND price_per_unit_dkk IS NOT NULL AND model IS NOT NULL "
        "ORDER BY model, gen, price_per_unit_dkk ASC"
    ).fetchall()

    groups: dict[tuple, list[dict]] = {}
    for item_key, model, gen, title, url, source, price, origin_country in rows:
        groups.setdefault((model, gen), []).append(
            {
                "item_key": item_key,
                "title": title,
                "url": url,
                "source": source,
                "price_per_unit_dkk": price,
                "origin_country": origin_country,
            }
        )

    now = datetime.datetime.now(datetime.UTC).isoformat()
    pairs = []
    for (model, gen), listings in groups.items():
        target = _pair_threshold(model, gen, thresholds, mk1_beater)
        if target is None:
            continue
        near_miss_max = target * (1 + near_miss_pct / 100)

        best = None
        for i in range(len(listings)):
            for j in range(i + 1, len(listings)):
                a, b = listings[i], listings[j]
                if same_region_only:
                    region_a = _region_key(a["origin_country"], eu_country_codes)
                    region_b = _region_key(b["origin_country"], eu_country_codes)
                    if region_a != region_b:
                        continue
                combined = a["price_per_unit_dkk"] + b["price_per_unit_dkk"]
                if combined > near_miss_max:
                    continue
                if best is None or combined < best["combined_price_dkk"]:
                    best = {
                        "model": model,
                        "gen": gen,
                        "item_key_1": a["item_key"],
                        "item_key_2": b["item_key"],
                        "title_1": a["title"],
                        "title_2": b["title"],
                        "url_1": a["url"],
                        "url_2": b["url"],
                        "source_1": a["source"],
                        "source_2": b["source"],
                        "combined_price_dkk": combined,
                        "target_price_dkk": target,
                        "distance_to_target_dkk": combined - target,
                        "computed_at": now,
                    }
        if best:
            pairs.append(best)

    pairs.sort(key=lambda p: p["distance_to_target_dkk"])
    return pairs


def sync_mixed_pairs_to_turso(turso, pairs: list[dict]) -> None:
    """Fuldt genberegnet hver køring -- ikke akkumulerende. DELETE + batch-
    INSERT er sikkert her (ingen anden proces skriver til denne tabel)."""
    turso.execute(MIXED_PAIRS_SCHEMA)
    turso.execute("DELETE FROM mixed_pairs")
    if not pairs:
        return
    turso.batch(
        [
            (
                "INSERT INTO mixed_pairs (model, gen, item_key_1, item_key_2, title_1, "
                "title_2, url_1, url_2, source_1, source_2, combined_price_dkk, "
                "target_price_dkk, distance_to_target_dkk, computed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    p["model"], p["gen"], p["item_key_1"], p["item_key_2"],
                    p["title_1"], p["title_2"], p["url_1"], p["url_2"],
                    p["source_1"], p["source_2"], p["combined_price_dkk"],
                    p["target_price_dkk"], p["distance_to_target_dkk"], p["computed_at"],
                ),
            )
            for p in pairs
        ]
    )
