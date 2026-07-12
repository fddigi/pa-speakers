"""Reverb: officielt API (api.reverb.com), foretraekkes frem for scraping. Haandterer paginering."""
import logging
import time

import requests

logger = logging.getLogger("pa_monitor.reverb")

API_BASE = "https://api.reverb.com/api"
ACCEPT_VERSION = "3.0"
TIMEOUT_S = 15
MAX_PAGES_PER_TERM = 5
REQUEST_DELAY_S = 1.0


def _headers() -> dict:
    return {
        "Accept": "application/hal+json",
        "Accept-Version": ACCEPT_VERSION,
        "Content-Type": "application/hal+json",
    }


def _fetch_page(session: requests.Session, query: str, page: int, condition: str | None) -> dict:
    params = {"query": query, "page": page, "per_page": 50}
    if condition:
        params["condition"] = condition
    resp = session.get(
        f"{API_BASE}/listings",
        params=params,
        headers=_headers(),
        timeout=TIMEOUT_S,
    )
    resp.raise_for_status()
    return resp.json()


def _infer_origin_country(item: dict, eu_country_codes: set) -> str | None:
    """Reverbs soegeresultater indeholder ikke saelgerens land direkte, men
    shipping.rates[].region_code afsloerer det ofte: en EU-landekode betyder EU-
    saelger; kun "US_*"-regioner (US_CON/US_AK/US_HI/US_PR) betyder US-saelger.
    Ukendt/tvetydigt -> None (ingen import-tillaeg beregnes, for ikke fejlagtigt
    at paalaegge en EU-saelger told+moms)."""
    rates = item.get("shipping", {}).get("rates", [])
    for rt in rates:
        code = (rt.get("region_code") or "").upper()
        if code in eu_country_codes:
            return code
    for rt in rates:
        code = (rt.get("region_code") or "").upper()
        if code.startswith("US_"):
            return "US"
    return None


def fetch(config: dict, dry_run: bool = False) -> list[dict]:
    """Returnerer raw listings: title/description/price_amount/price_currency/url/extra."""
    search_terms = config["search_terms"]["primary"] + config["search_terms"].get("secondary", [])
    eu_country_codes = set(config.get("import_costs", {}).get("eu_country_codes", []))
    reverb_cfg = config.get("reverb", {})
    condition = reverb_cfg.get("condition")
    exclude_origin_countries = {
        c.upper() for c in reverb_cfg.get("exclude_origin_countries", [])
    }
    raw_listings = []
    excluded_count = 0

    with requests.Session() as session:
        for term in search_terms:
            try:
                page = 1
                while page <= MAX_PAGES_PER_TERM:
                    logger.info(
                        "Reverb: soeger '%s' side %d (condition=%s)",
                        term, page, condition or "alle",
                    )
                    data = _fetch_page(session, term, page, condition)
                    listings = data.get("listings", [])
                    if not listings:
                        break

                    for item in listings:
                        title = item.get("title", "")
                        price_info = item.get("price") or {}
                        amount_str = price_info.get("amount")
                        currency = price_info.get("currency", "USD")
                        if amount_str is None:
                            continue
                        try:
                            amount = float(amount_str)
                        except (TypeError, ValueError):
                            continue

                        origin_country_code = _infer_origin_country(item, eu_country_codes)
                        if origin_country_code in exclude_origin_countries:
                            excluded_count += 1
                            continue

                        url = item.get("_links", {}).get("web", {}).get("href", "")
                        description = item.get("description", "") or ""

                        raw_listings.append({
                            "title": title,
                            "description": description,
                            "price_amount": amount,
                            "price_currency": currency,
                            "url": url,
                            "origin_country_code": origin_country_code,
                            "extra": {
                                "search_term": term,
                                "reverb_id": item.get("id"),
                                "condition": item.get("condition", {}).get("slug"),
                            },
                        })

                    total_pages = data.get("total_pages", page)
                    if page >= total_pages:
                        break
                    page += 1
                    time.sleep(REQUEST_DELAY_S)

            except requests.RequestException:
                logger.exception(
                    "Reverb: fejl ved soegning efter '%s', springer denne soegning over", term
                )
                continue

            time.sleep(REQUEST_DELAY_S)

    if excluded_count:
        logger.info(
            "Reverb: %d annonce(r) droppet (oprindelse i %s)",
            excluded_count, exclude_origin_countries,
        )

    return raw_listings
