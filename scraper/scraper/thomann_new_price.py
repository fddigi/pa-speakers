"""F11-spike: Thomann NYPRIS-reference (ikke B-Stock).

Konklusion fra spiken (se FEATURES.md F11): Thomanns almindelige produktside
(fx thomann.de/de/rcf_art_910_a.htm, IKKE B-Stock-varianten som thomann.py
allerede håndterer) viser nyprisen i statisk HTML og kan parses med samme
selector som `_fetch_price_dkk` i sources/thomann.py. Bekræftet empirisk
2026-07-13 for RCF ART 910-A (579 EUR).

Rollen er BEVIDST kun et DISPLAY-ANKER ("nypris: X kr" ved siden af
brugtannoncer) -- IKKE input til percentil-klassifikationen. Nypriser ville
forurene den brugte prisfordeling og skæve klassifikationen (se FEATURES.md).
Derfor sin egen tabel (`thomann_new_price_ref`), helt adskilt fra `listings`.

MODEL_NEW_PRICE_URLS er bevidst kun udfyldt med den ene model der er
empirisk bekræftet. Thomann rate-limiter (429) ved flere hurtige forespørgsler
i træk -- yderligere modeller (710A/708A/SUB705/712A/Yamaha DXR-serien) kræver
hver deres egen URL-slug bekræftet enkeltvis, med god tid imellem, fremfor at
gætte mønstre i en burst (det er præcis hvad der udløste 429'erne under
spiken). Tilføj nye modeller her når/hvis en slug er bekræftet -- resten af
koden understøtter allerede en vilkårlig mapping uden ændringer.
"""
from __future__ import annotations

import datetime
import logging
import re

import requests
from bs4 import BeautifulSoup
from scraper_core.pricing import parse_price

logger = logging.getLogger("pa_monitor.thomann_new_price")

TIMEOUT_S = 15

MODEL_NEW_PRICE_URLS = {
    "910a": "https://www.thomann.de/de/rcf_art_910_a.htm",
}

NEW_PRICE_SCHEMA = """
CREATE TABLE IF NOT EXISTS thomann_new_price_ref (
    model_key TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    price_eur REAL NOT NULL,
    price_dkk REAL NOT NULL,
    checked_at TEXT NOT NULL
);
"""


def _fetch_price_eur(session: requests.Session, url: str):
    try:
        resp = session.get(url, timeout=TIMEOUT_S, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except requests.RequestException:
        logger.exception("Thomann nypris: kunne ikke hente %s", url)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    price_el = soup.select_one(".price-and-availability .price-wrapper .price")
    if not price_el:
        return None
    m = re.search(r"([\d.,]+)\s*€", price_el.get_text())
    if not m:
        return None
    return parse_price(m.group(1), unit="major", decimal_style="comma")


def fetch_new_prices(eur_dkk: float) -> list[dict]:
    """Henter nypris for hver kendt model i MODEL_NEW_PRICE_URLS. Fejlede
    hentninger (429/timeout/ændret markup) springes helt over -- se
    sync_thomann_new_price_to_turso, som bevidst IKKE overskriver en
    eksisterende reference med en fejlet hentning."""
    refs = []
    now = datetime.datetime.now(datetime.UTC).isoformat()
    with requests.Session() as session:
        for model_key, url in MODEL_NEW_PRICE_URLS.items():
            price_eur = _fetch_price_eur(session, url)
            if price_eur is None:
                logger.warning(
                    "Thomann nypris: kunne ikke hente pris for %s, springer over "
                    "denne koersel (beholder evt. tidligere reference)",
                    model_key,
                )
                continue
            refs.append({
                "model_key": model_key,
                "url": url,
                "price_eur": price_eur,
                "price_dkk": round(price_eur * eur_dkk, 2),
                "checked_at": now,
            })
    return refs


def sync_thomann_new_price_to_turso(turso, refs: list[dict]) -> None:
    """Upsert pr. model -- modsat mixed_pairs (F6) er dette IKKE en fuld
    genberegning: en fejlet hentning for én model denne koersel maa aldrig
    slette en tidligere kendt reference for den model."""
    turso.execute(NEW_PRICE_SCHEMA)
    if not refs:
        return
    turso.batch(
        [
            (
                "INSERT INTO thomann_new_price_ref (model_key, url, price_eur, price_dkk, "
                "checked_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(model_key) DO UPDATE SET "
                "url=excluded.url, price_eur=excluded.price_eur, "
                "price_dkk=excluded.price_dkk, checked_at=excluded.checked_at",
                (r["model_key"], r["url"], r["price_eur"], r["price_dkk"], r["checked_at"]),
            )
            for r in refs
        ]
    )
