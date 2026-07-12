"""Thomann B-Stock: poll den samlede RCF-kategoriside paa thomann.dk (native DKK-priser).

Design-note (fundet ved research, se README): Thomann's per-model B-Stock-produktsider
(f.eks. rcf_art_708_a_mk_v_b_stock.htm) findes KUN som live URL saa laenge der faktisk
er B-Stock paa lager -- ellers 404'er de. Der er derfor intet stabilt "ikke paa lager"-
produkt-URL at polle direkte, saa vi poller i stedet kategorisiden
(rcf_aktive_fullrange_hoejttalere.html), som altid findes og lister alle aktuelt
tilgaengelige RCF-produkter, inkl. B-Stock-varianter naar de findes.

Lagerstatus spores i sin egen tabel `thomann_stock_state` (keyed by produkt-url) --
adskilt fra `listings`-dedup, som er for permanente annonce-hits. Rapporterer
overgang fra "ikke i kategorilisten" til "i kategorilisten" (= paa lager).
"""
import datetime
import logging
import re
import sqlite3

import requests
from bs4 import BeautifulSoup
from scraper_core.matching import normalize_model_number
from scraper_core.pricing import parse_price

logger = logging.getLogger("pa_monitor.thomann")

TIMEOUT_S = 15
BASE_URL = "https://www.thomann.dk/"

STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS thomann_stock_state (
    url TEXT PRIMARY KEY,
    in_stock INTEGER NOT NULL,
    price_dkk REAL,
    last_checked TEXT
)
"""

def _get_previous_in_stock(conn: sqlite3.Connection, url: str):
    row = conn.execute("SELECT in_stock FROM thomann_stock_state WHERE url = ?", (url,)).fetchone()
    return bool(row[0]) if row else None


def _save_state(conn: sqlite3.Connection, url: str, in_stock: bool, price_dkk, dry_run: bool = False) -> None:
    if dry_run:
        return
    conn.execute(
        """
        INSERT INTO thomann_stock_state (url, in_stock, price_dkk, last_checked)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            in_stock=excluded.in_stock,
            price_dkk=excluded.price_dkk,
            last_checked=excluded.last_checked
        """,
        (url, int(in_stock), price_dkk, datetime.datetime.utcnow().isoformat()),
    )
    conn.commit()


def _matches_target_models(title: str, search_terms: list) -> bool:
    # Normaliserer BEGGE sider (title og search-term) foer substring-tjekket, saa
    # glued generation-suffixes (fx "ART-910A ... ART910A" i en rigtig titel fra
    # vores egen database) ikke skjuler et reelt match. Fundet 2026-07-12: raa
    # substring-sammenligning fejlede paa netop den slags titler, fordi
    # search-termets bogstavelige mellemrum ("art 910") ikke fandtes i titlen,
    # selvom modellen tydeligt er den samme. Se scraper_core.matching.
    normalized_title = normalize_model_number(title)
    for term in search_terms:
        # "RCF ART 910" -> "ART 910"
        bare = term.upper().replace("RCF", "").strip()
        normalized_term = normalize_model_number(bare)
        if normalized_term in normalized_title:
            return True
    return False


def _find_bstock_cards(html: str) -> list:
    """Returnerer [{'title':.., 'url':..}] for B-Stock-produkter i kategorilisten."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for card in soup.select("div.fx-product-list-entry"):
        title_el = card.select_one(".product__title")
        link_el = card.select_one("a.product__content, a.product__image")
        if not title_el or not link_el:
            continue
        title = title_el.get_text(" ", strip=True)
        if "b-stock" not in title.lower():
            continue
        href = link_el.get("href", "")
        url = href if href.startswith("http") else BASE_URL + href.lstrip("/")
        results.append({"title": title, "url": url})
    return results


def _fetch_price_dkk(session: requests.Session, url: str):
    try:
        resp = session.get(url, timeout=TIMEOUT_S, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except requests.RequestException:
        logger.exception("Thomann: kunne ikke hente pris fra %s", url)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    price_el = soup.select_one(".price-and-availability .price-wrapper .price")
    if not price_el:
        return None
    m = re.search(r"([\d.]+)\s*kr", price_el.get_text())
    if not m:
        return None
    # decimal_style FORCED to "comma" (never "auto") - same reasoning as
    # kleinanzeigen.py: Danish formatting has no reliable signal to distinguish
    # a thousands-only dot ("1.234" == 1234 kr) from a decimal dot ("47.26"),
    # and "auto" would misread the former as 1.234 kr.
    return parse_price(m.group(1), unit="major", decimal_style="comma")


def fetch(config: dict, dry_run: bool = False) -> list[dict]:
    """Returnerer raw listings KUN for B-Stock-produkter der er NYE i kategorilisten
    (dvs. skiftet fra 'ikke paa lager' til 'paa lager')."""
    category_url = config.get("thomann_category_url")
    if not category_url:
        logger.info("Thomann: ingen thomann_category_url konfigureret, springer over")
        return []

    search_terms = config["search_terms"]["primary"] + config["search_terms"].get("secondary", [])
    db_path = config.get("db_path", "seen.db")
    raw_listings = []

    conn = sqlite3.connect(db_path)
    conn.execute(STATE_SCHEMA)
    conn.commit()

    try:
        with requests.Session() as session:
            try:
                logger.info("Thomann: henter kategoriside %s", category_url)
                resp = session.get(category_url, timeout=TIMEOUT_S, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
            except requests.RequestException:
                logger.exception("Thomann: kunne ikke hente kategoriside, springer kilden over")
                return []

            all_bstock_cards = _find_bstock_cards(resp.text)
            matching_cards = [c for c in all_bstock_cards if _matches_target_models(c["title"], search_terms)]
            logger.info(
                "Thomann: %d B-Stock-produkter i kategorien, %d matcher vores modeller",
                len(all_bstock_cards), len(matching_cards),
            )

            currently_matching_urls = set()
            for card in matching_cards:
                url = card["url"]
                title = card["title"]
                currently_matching_urls.add(url)

                previous = _get_previous_in_stock(conn, url)
                price_dkk = _fetch_price_dkk(session, url)

                if price_dkk is None:
                    # Fejlet prishentning (fx 429/timeout) maa ALDRIG rapporteres som en
                    # 0-kr-annonce -- det ville se ud som et falsk GODT KØB. Spring over
                    # uden at gemme tilstand, saa vi proever igen (og rapporterer korrekt)
                    # naeste koersel.
                    logger.warning("Thomann: kunne ikke hente pris for %s, proever igen naeste koersel", url)
                    continue

                if previous is not True:
                    raw_listings.append({
                        "title": f"Thomann B-Stock: {title}",
                        "description": "B-Stock skiftet til paa lager (kategoriliste)",
                        "price_amount": price_dkk,
                        "price_currency": "DKK",
                        "url": url,
                        "origin_country_code": "DK",
                        "extra": {"transition": "out_to_in_stock"},
                    })

                _save_state(conn, url, True, price_dkk, dry_run=dry_run)

            # Produkter der tidligere var paa lager men ikke laengere er i kategorilisten
            # markeres som ikke-paa-lager, saa en senere genkomst rapporteres som nyt.
            previously_seen = conn.execute(
                "SELECT url FROM thomann_stock_state WHERE in_stock = 1"
            ).fetchall()
            for (seen_url,) in previously_seen:
                if seen_url not in currently_matching_urls:
                    _save_state(conn, seen_url, False, None, dry_run=dry_run)
    finally:
        conn.close()

    return raw_listings
