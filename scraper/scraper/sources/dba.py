"""DBA.dk: samme tilgang som Blocket -- Playwright headful, throttlet, best-effort.

F2 (BACKLOG.md): DBA.dk har IKKE laengere sit eget RSS-system -- soegninger
omdirigerer til /recommerce/forsale/search, samme Schibsted-platform som Blocket.se
(bekraeftet 2026-07-08: identisk markup, "sf-search-ad"-klasser). Ingen RSS at polle,
men triviel at scrape med samme tilgang som Blocket, jf. den oprindelige specs
alternativ ("RSS/HTML-poll").

Fejler ALDRIG hele scriptet: bot-wall eller andre problemer logges og giver blot en tom
liste for denne koersel.

NB: DBAs DOM-struktur aendrer sig jaevnligt -- CSS-selectors herunder er best-effort
og kan kraeve justering hvis siden aendres. Bot-wall-detektionen fejler graceful i så fald.
"""
import logging
import random
import re
import time
from urllib.parse import quote

logger = logging.getLogger("pa_monitor.dba")

BASE_URL = "https://www.dba.dk"
SEARCH_URL_TMPL = BASE_URL + "/recommerce/forsale/search?q={query}"

BOT_WALL_MARKERS = ["captcha", "for mange foresp", "unusual traffic", "access denied", "er du en robot"]


def _build_search_url(term: str) -> str:
    return SEARCH_URL_TMPL.format(query=quote(term))


def _looks_like_bot_wall(page) -> bool:
    content = page.content().lower()
    return any(m in content for m in BOT_WALL_MARKERS)


def _parse_price(price_text: str):
    m = re.search(r"([\d\s.]+)\s*kr", price_text or "", re.I)
    if not m:
        return None, "DKK"
    amount_str = m.group(1).replace(" ", "").replace("\xa0", "").replace(".", "")
    try:
        return float(amount_str), "DKK"
    except ValueError:
        return None, "DKK"


def _parse_listing_cards(page):
    """Selectors bekraeftet mod reelt DBA-markup 2026-07-08: annonce-kort er
    <article class="... sf-search-ad ...">, titel i <h2>, link i <a class="sf-search-ad-link">
    (allerede absolut URL), pris i en <div class="... font-bold ..."> som "4.600 kr"."""
    cards = page.query_selector_all("article.sf-search-ad")
    results = []
    for card in cards:
        try:
            title_el = card.query_selector("h2")
            link_el = card.query_selector("a.sf-search-ad-link")
            if not title_el or not link_el:
                continue
            title = title_el.inner_text().strip()
            price_el = card.query_selector(".font-bold")
            price_text = price_el.inner_text() if price_el else card.inner_text()
            url = link_el.get_attribute("href")
            results.append({"title": title, "price_text": price_text, "url": url})
        except Exception:
            logger.exception("DBA: kunne ikke parse et annonce-kort, springer over")
    return results


def fetch(config: dict, dry_run: bool = False) -> list[dict]:
    """Returnerer raw listings: title/description/price_amount/price_currency/url/extra."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("DBA: playwright er ikke installeret, springer kilden over")
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
                locale="da-DK",
            )
            page = context.new_page()

            pages_fetched = 0
            for term in search_terms:
                if pages_fetched >= max_pages:
                    logger.info("DBA: naaet max_pages_total (%d), stopper for denne koersel", max_pages)
                    break
                try:
                    url = _build_search_url(term)
                    logger.info("DBA: henter '%s' -> %s", term, url)
                    page.goto(url, timeout=20000)
                    pages_fetched += 1

                    try:
                        page.wait_for_selector("article.sf-search-ad", timeout=6000)
                    except Exception:
                        pass  # ingen kort dukkede op -- afklares nedenfor som normalt

                    if _looks_like_bot_wall(page):
                        logger.warning(
                            "DBA: bot-wall/CAPTCHA moedt for '%s', springer kilden over for denne koersel",
                            term,
                        )
                        break

                    for card in _parse_listing_cards(page):
                        amount, currency = _parse_price(card["price_text"])
                        if amount is None:
                            continue
                        raw_listings.append({
                            "title": card["title"],
                            "description": "",
                            "price_amount": amount,
                            "price_currency": currency,
                            "url": card["url"],
                            "origin_country_code": "DK",
                            "extra": {"search_term": term, "source_page": url},
                        })

                    time.sleep(random.uniform(min_delay, max_delay))
                except Exception:
                    logger.exception("DBA: fejl under haandtering af '%s', springer over", term)
                    continue

            context.close()
            browser.close()
    except Exception:
        logger.exception("DBA: kilden fejlede helt, springer kilden over for denne koersel")
        return []

    return raw_listings
