"""Gearloop.se (F11 spike): svensk brugtmarkedsplads for musikudstyr, med
egne kategorier "PA & Live" og "Studio & Scenutrustning" -- direkte relevant
for RCF ART/Yamaha DXR-segmentet, samme SE-marked som Blocket.

Samme tilgang som Blocket/Kleinanzeigen/DBA -- Playwright, throttlet,
best-effort, fejler ALDRIG hele scriptet.

Empirisk bekræftet 2026-07-13 (F11-spiken): siden er en Next.js App
Router-app -- søgeresultater er IKKE til stede i den statiske HTML (kun
søgefeltets egen ekko af query-parameteren), de renderes klientsidet efter
hydration. En almindelig `requests`-baseret hentning (som Reverb/Thomann)
ville derfor ALTID give 0 resultater uden at fejle synligt -- Playwright er
nødvendig her, ikke valgfrit. `robots.txt` tillader generel crawling
("Allow: /"), kun MJ12bot har en specifik crawl-delay.

NB: Gearloops DOM-struktur kan ændre sig -- CSS-selectors herunder er
best-effort, bekræftet mod reelt markup på hentningstidspunktet, og kan
kræve justering hvis siden ændres. Bot-wall-detektionen fejler graceful i så
fald (Gearloop har aldrig faktisk vist os en bot-wall under research -
markørerne herunder er defensive gæt, samme mønster som Blocket/DBA).
"""
import logging
import random
import re
import time
from urllib.parse import quote

logger = logging.getLogger("pa_monitor.gearloop")

BASE_URL = "https://gearloop.se"
SEARCH_URL_TMPL = BASE_URL + "/search?q={query}"

BOT_WALL_MARKERS = [
    "captcha", "unusual traffic", "access denied", "är du en robot", "for many requests",
]


def _build_search_url(term: str) -> str:
    return SEARCH_URL_TMPL.format(query=quote(term))


def _looks_like_bot_wall(page) -> bool:
    content = page.content().lower()
    return any(m in content for m in BOT_WALL_MARKERS)


def _parse_price(price_text: str):
    # Samme format som Blocket: mellemrum (inkl. \xa0) som tusindtalsseparator,
    # ingen decimal-del i praksis (brugtudstyr prissættes i hele SEK).
    m = re.search(r"([\d\s\xa0]+)\s*kr", price_text or "", re.I)
    if not m:
        return None, "SEK"
    amount_str = m.group(1).replace(" ", "").replace("\xa0", "")
    try:
        return float(amount_str), "SEK"
    except ValueError:
        return None, "SEK"


def _parse_listing_cards(page):
    """Selectors bekræftet mod reelt Gearloop-markup 2026-07-13: annonce-kort
    er <article>, titel i <h3><a>, den fulde kort-link er en usynlig
    <a aria-hidden="true"> med RELATIV href (ikke absolut som Blocket), pris
    findes et sted i kortets tekst som "1 500 kr" (mellemrum-separeret SEK,
    ingen decimaler i praksis)."""
    cards = page.query_selector_all("article")
    results = []
    for card in cards:
        try:
            title_el = card.query_selector("h3 a")
            link_el = card.query_selector("a[aria-hidden='true']")
            if not title_el or not link_el:
                continue
            title = title_el.inner_text().strip()
            href = link_el.get_attribute("href")
            if not href:
                continue
            url = href if href.startswith("http") else BASE_URL + href
            desc_el = card.query_selector("p")
            description = desc_el.inner_text().strip() if desc_el else ""
            # Ingen stabil pris-specifik selector fundet (Tailwind-utility-
            # klasser, sandsynligvis skrøbelige over tid) -- samme
            # fallback-mønster som Blockets `price_el or card.inner_text()`:
            # regex'en i _parse_price er specifik nok (kræver "kr" lige efter
            # tallet) til at være sikker mod hele kortets tekst.
            price_text = card.inner_text()
            results.append({
                "title": title, "description": description,
                "price_text": price_text, "url": url,
            })
        except Exception:
            logger.exception("Gearloop: kunne ikke parse et annonce-kort, springer over")
    return results


def fetch(config: dict, dry_run: bool = False) -> list[dict]:
    """Returnerer raw listings: title/description/price_amount/price_currency/url/extra."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Gearloop: playwright er ikke installeret, springer kilden over")
        return []

    pw_cfg = config.get("playwright", {})
    min_delay = pw_cfg.get("min_delay_s", 3)
    max_delay = pw_cfg.get("max_delay_s", 8)
    max_pages = pw_cfg.get("max_pages_total", 20)
    headless = pw_cfg.get("headless", False)

    search_terms = config["search_terms"]["primary"] + config["search_terms"].get("secondary", [])
    raw_listings = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
                locale="sv-SE",
            )
            page = context.new_page()

            pages_fetched = 0
            for term in search_terms:
                if pages_fetched >= max_pages:
                    logger.info(
                        "Gearloop: naaet max_pages_total (%d), stopper for denne koersel",
                        max_pages,
                    )
                    break
                try:
                    url = _build_search_url(term)
                    logger.info("Gearloop: henter '%s' -> %s", term, url)
                    page.goto(url, timeout=20000)
                    pages_fetched += 1
                    # Søgeresultater hydrerer client-side EFTER load - se
                    # modulets docstring. Uden denne ventetid ville vi parse
                    # den tomme pre-hydration-DOM og altid faa 0 kort.
                    page.wait_for_timeout(2500)

                    if _looks_like_bot_wall(page):
                        logger.warning(
                            "Gearloop: bot-wall/CAPTCHA moedt for '%s', springer kilden over "
                            "for denne koersel",
                            term,
                        )
                        break

                    for card in _parse_listing_cards(page):
                        amount, currency = _parse_price(card["price_text"])
                        if amount is None:
                            continue
                        raw_listings.append({
                            "title": card["title"],
                            "description": card["description"],
                            "price_amount": amount,
                            "price_currency": currency,
                            "url": card["url"],
                            "origin_country_code": "SE",
                            "extra": {"search_term": term, "source_page": url},
                        })

                    time.sleep(random.uniform(min_delay, max_delay))
                except Exception:
                    logger.exception(
                        "Gearloop: fejl under haandtering af '%s', springer over", term
                    )
                    continue

            context.close()
            browser.close()
    except Exception:
        logger.exception("Gearloop: kilden fejlede helt, springer kilden over for denne koersel")
        return []

    return raw_listings
