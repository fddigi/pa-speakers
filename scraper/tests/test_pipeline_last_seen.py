"""F13 (2026-07-22): "skjul solgte annoncer" i frontend'en er baseret på
`last_seen` -- en annonce der IKKE er fundet i en scrape-køring skal ikke få
last_seen opdateret, mens en der STADIG dukker op (uanset om noget ved den
har ændret sig) skal. Uden denne test kunne en regression i pipeline.py
tavst stoppe med at røre last_seen for uændrede rækker, hvilket ville få
ALLE stadig-aktive annoncer til at fremstå som solgte efter kun én køring.
"""
from __future__ import annotations

import time

from scraper_core.local_db import LocalStore

from scraper.pipeline import run_source

CONFIG = {
    "currency": {"eur_dkk": 7.46, "sek_dkk": 0.70, "usd_dkk": 6.90},
    "thresholds": {},
    "mk1_beater": {"godt_koeb_max": 2500},
    "studio_sub_thresholds": {
        "genelec_7050c": {
            "godt_koeb_max": 4000, "fair_min": 4000, "fair_max": 5000, "overpriced_min": 5500,
        },
    },
}


def _fake_fetch(config, dry_run=False):
    return [{
        "title": "Genelec 7050C subwoofer",
        "description": "",
        "price_amount": 4500,
        "price_currency": "DKK",
        "url": "https://example.com/ad/1",
        "extra": {},
    }]


def test_new_listing_gets_last_seen_equal_to_first_seen(tmp_path):
    store = LocalStore(tmp_path / "local.db")
    run_source(store, "reverb", _fake_fetch, CONFIG)

    row = store.connection.execute("SELECT first_seen, last_seen FROM listings").fetchone()
    assert row["last_seen"] == row["first_seen"]
    store.close()


def test_last_seen_advances_on_unchanged_rerun_but_first_seen_does_not(tmp_path):
    store = LocalStore(tmp_path / "local.db")
    run_source(store, "reverb", _fake_fetch, CONFIG)
    row = store.connection.execute("SELECT first_seen, last_seen FROM listings").fetchone()
    first_seen_1, last_seen_1 = row["first_seen"], row["last_seen"]

    time.sleep(0.01)
    run_source(store, "reverb", _fake_fetch, CONFIG)  # samme annonce, ingen ændring

    row = store.connection.execute("SELECT first_seen, last_seen FROM listings").fetchone()
    assert row["first_seen"] == first_seen_1
    assert row["last_seen"] > last_seen_1
    store.close()
