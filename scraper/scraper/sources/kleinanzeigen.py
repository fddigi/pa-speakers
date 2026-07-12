"""Kleinanzeigen.de: intet API, aggressiv bot-beskyttelse -> Playwright headful,
throttlet, best-effort.

Fejler ALDRIG hele scriptet: bot-wall eller andre problemer logges og giver blot en tom
liste for denne koersel. Se pa-monitor-claude-code-prompt_1.md, punkt 3.
"""
import logging
import random
import re
import time
from urllib.parse import quote

from scraper_core.pricing import parse_price

logger = logging.getLogger("pa_monitor.kleinanzeigen")

BASE_URL = "https://www.kleinanzeigen.de"

BOT_WALL_MARKERS = ["captcha", "unusual traffic", "bot check", "access denied", "geo.captcha"]


def _build_search_url(term: str, page_num: int = 1) -> str:
    """Side 1: /s-<query>/k0. Side 2+: /s-seite:<n>/<query>/k0 (bekraeftet mod
    live site 2026-07-08 via pagination-nav'ens <a href> for kendte flersidede
    soegninger)."""
    query = quote(term.replace(" ", "-"))
    if page_num <= 1:
        return f"{BASE_URL}/s-{query}/k0"
    return f"{BASE_URL}/s-seite:{page_num}/{query}/k0"


def _looks_like_bot_wall(page) -> bool:
    content = page.content().lower()
    return any(m in content for m in BOT_WALL_MARKERS)


def _parse_price(price_text: str):
    m = re.search(r"([\d.,]+)\s*€", price_text or "")
    if not m:
        return None, "EUR"
    # decimal_style FORCED to "comma" (never "auto"): German formatting has no
    # reliable self-disambiguating signal for a thousands-only dot ("1.234" ==
    # 1234) vs. a decimal dot ("47.26" == 47.26) - "auto" guesses the latter
    # whenever there's a dot and no comma, which would silently turn e.g.
    # "1.234 €" into 1.234 EUR instead of 1234 EUR. Kleinanzeigen always uses
    # dot-as-thousands/comma-as-decimal, so this must be forced explicitly.
    return parse_price(m.group(1), unit="major", decimal_style="comma"), "EUR"


def _parse_listing_cards(page):
    cards = page.query_selector_all("article.aditem")
    results = []
    for card in cards:
        try:
            title_el = card.query_selector(".text-module-begin")
            price_el = card.query_selector(".aditem-main--middle--price-shipping--price")
            link_el = card.query_selector("a.ellipsis")
            if not title_el or not link_el:
                continue
            title = title_el.inner_text().strip()
            price_text = price_el.inner_text().strip() if price_el else ""
            href = link_el.get_attribute("href")
            url = BASE_URL + href if href and href.startswith("/") else href
            results.append({"title": title, "price_text": price_text, "url": url})
        except Exception:
            logger.exception("Kleinanzeigen: kunne ikke parse et annonce-kort, springer over")
    return results


def fetch(config: dict, dry_run: bool = False) -> list[dict]:
    """Returnerer raw listings: title/description/price_amount/price_currency/url/extra."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Kleinanzeigen: playwright er ikke installeret, springer kilden over")
        return []

    pw_cfg = config.get("playwright", {})
    min_delay = pw_cfg.get("min_delay_s", 3)
    max_delay = pw_cfg.get("max_delay_s", 8)
    max_pages_per_term = pw_cfg.get("max_pages_per_term", 3)
    max_pages_total = pw_cfg.get("max_pages_total", 20)
    headless = pw_cfg.get("headless", False)

    search_terms = config["search_terms"]["primary"] + config["search_terms"].get("secondary", [])
    raw_listings = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)

            pages_fetched_total = 0
            bot_wall_hit = False
            for term in search_terms:
                if bot_wall_hit:
                    break
                if pages_fetched_total >= max_pages_total:
                    logger.info(
                        "Kleinanzeigen: naaet max_pages_total (%d), stopper for denne koersel",
                        max_pages_total,
                    )
                    break

                for page_num in range(1, max_pages_per_term + 1):
                    if pages_fetched_total >= max_pages_total:
                        logger.info(
                            "Kleinanzeigen: naaet max_pages_total (%d), stopper for denne koersel",
                            max_pages_total,
                        )
                        break

                    # FRISK context+side PR. FORESPOERGSEL: Kleinanzeigen degraderer
                    # stille resultater til 0 kort fra og med 2. forespoergsel i en
                    # delt browser-context/session -- INGEN bot-wall-tekst, bare
                    # tomme resultater. Bekraeftet empirisk 2026-07-08: samme
                    # soegning gav 9-12 kort som foerste forespoergsel i en frisk
                    # context, men 0 som 2.-3. forespoergsel i en genbrugt context
                    # (uafhaengigt af om page-objektet selv blev genbrugt eller ej).
                    context = browser.new_context(
                        user_agent=(
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                        ),
                        viewport={"width": 1280, "height": 900},
                        locale="de-DE",
                    )
                    page = context.new_page()
                    try:
                        url = _build_search_url(term, page_num)
                        logger.info("Kleinanzeigen: henter '%s' side %d -> %s", term, page_num, url)
                        page.goto(url, timeout=20000)
                        pages_fetched_total += 1

                        # page.goto() venter kun paa 'load'-eventet, men annoncekortene
                        # kan hydrere client-side EFTER load.
                        try:
                            page.wait_for_selector("article.aditem", timeout=6000)
                        except Exception:
                            pass  # ingen kort dukkede op -- afklares nedenfor som normalt

                        if _looks_like_bot_wall(page):
                            logger.warning(
                                "Kleinanzeigen: bot-wall/CAPTCHA moedt for '%s' side %d, "
                                "springer kilden over for denne koersel",
                                term, page_num,
                            )
                            bot_wall_hit = True
                            break

                        cards = _parse_listing_cards(page)
                        if not cards:
                            logger.info(
                                "Kleinanzeigen: '%s' side %d gav 0 kort, sidste side naaet",
                                term, page_num,
                            )
                            break

                        for card in cards:
                            amount, currency = _parse_price(card["price_text"])
                            if amount is None:
                                continue
                            raw_listings.append({
                                "title": card["title"],
                                "description": "",
                                "price_amount": amount,
                                "price_currency": currency,
                                "url": card["url"],
                                "origin_country_code": "DE",
                                "extra": {"search_term": term, "source_page": url},
                            })

                        time.sleep(random.uniform(min_delay, max_delay))
                    except Exception:
                        logger.exception(
                            "Kleinanzeigen: fejl under haandtering af '%s' side %d, springer over",
                            term, page_num,
                        )
                        break
                    finally:
                        context.close()

            browser.close()
    except Exception:
        logger.exception(
            "Kleinanzeigen: kilden fejlede helt, springer kilden over for denne koersel"
        )
        return []

    return raw_listings
