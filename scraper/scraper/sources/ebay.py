"""F4: eBay.de som kilde. eBay Browse API (officiel, gratis nøgle via
developer.ebay.com) -- BRUGER-AFHÆNGIGHED: en `EBAY_APP_ID`/`EBAY_CERT_ID`
(Client ID/Client Secret) skal oprettes af brugeren selv på developer.ebay.com
og sættes som miljøvariabler. Denne fil kan IKKE gøre det for brugeren
(kræver egen eBay-konto + accept af eBay's developer-vilkår).

OAuth2 client-credentials flow (application token, intet bruger-login) --
tokenet caches kun i hukommelsen for denne ene proces-kørsel (ikke persisteret),
matcher FEATURES.md F4's design.

Auktioner (buyingOptions indeholder "AUCTION"): nuværende bud er IKKE en
landed pris -- en lav budpris nu betyder ikke annoncen reelt kan købes for det.
Håndteres i pipeline.py (`listing["raw"]["is_auction"]`), IKKE her -- denne
fil markerer blot annoncen, klassificerer aldrig selv.
"""
from __future__ import annotations

import base64
import logging
import os
import time

import requests

logger = logging.getLogger("pa_monitor.ebay")

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
MARKETPLACE_ID = "EBAY_DE"
OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"
TIMEOUT_S = 15
REQUEST_DELAY_S = 1.0

_token_cache: dict = {"access_token": None, "expires_at": 0.0}


def _get_access_token(session: requests.Session, app_id: str, cert_id: str) -> str | None:
    """OAuth2 client-credentials -- cachet i modul-global hukommelse for denne
    proces' levetid (matcher FEATURES.md F4: "Token caches i memory pr. kørsel"),
    aldrig skrevet til disk."""
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["access_token"]

    credentials = base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()
    try:
        resp = session.post(
            TOKEN_URL,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials", "scope": OAUTH_SCOPE},
            timeout=TIMEOUT_S,
        )
        resp.raise_for_status()
    except requests.RequestException:
        logger.exception("eBay: kunne ikke hente OAuth2-token (ugyldig/manglende nøgle?)")
        return None

    data = resp.json()
    token = data.get("access_token")
    if not token:
        logger.warning("eBay: token-svar indeholdt intet access_token, springer kilden over")
        return None

    # -30s margin: undgaar at et token der lige akkurat er udloebet naar det
    # bruges i det efterfoelgende API-kald, sender os i en unoedvendig 401-retry.
    _token_cache["access_token"] = token
    _token_cache["expires_at"] = time.time() + data.get("expires_in", 7200) - 30
    return token


def _fetch_search(session: requests.Session, token: str, query: str) -> list[dict]:
    resp = session.get(
        SEARCH_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE_ID,
        },
        params={"q": query, "filter": "conditions:{USED}", "limit": 50},
        timeout=TIMEOUT_S,
    )
    resp.raise_for_status()
    return resp.json().get("itemSummaries", [])


def fetch(config: dict, dry_run: bool = False) -> list[dict]:
    """Returnerer raw listings: title/description/price_amount/price_currency/url/extra.

    Manglende/ugyldig `EBAY_APP_ID`/`EBAY_CERT_ID`: logger og springer kilden
    helt over (samme mønster som Gearloops manglende-Playwright-håndtering) --
    krasjer ALDRIG resten af køringen (se pipeline.py's per-kilde try/except)."""
    app_id = os.environ.get("EBAY_APP_ID")
    cert_id = os.environ.get("EBAY_CERT_ID")
    if not app_id or not cert_id:
        logger.info(
            "eBay: EBAY_APP_ID/EBAY_CERT_ID ikke sat, springer kilden over "
            "(se README.md for opsætning via developer.ebay.com)"
        )
        return []

    search_terms = config["search_terms"]["primary"] + config["search_terms"].get("secondary", [])
    raw_listings = []

    with requests.Session() as session:
        token = _get_access_token(session, app_id, cert_id)
        if token is None:
            return []

        for term in search_terms:
            try:
                logger.info("eBay: søger '%s' (marketplace=%s)", term, MARKETPLACE_ID)
                items = _fetch_search(session, token, term)
            except requests.RequestException:
                logger.exception("eBay: fejl ved søgning efter '%s', springer over", term)
                continue

            for item in items:
                price_info = item.get("price") or {}
                amount_str = price_info.get("value")
                currency = price_info.get("currency", "EUR")
                if amount_str is None:
                    continue
                try:
                    amount = float(amount_str)
                except (TypeError, ValueError):
                    continue

                buying_options = item.get("buyingOptions", [])
                is_auction = "AUCTION" in buying_options

                raw_listings.append({
                    "title": item.get("title", ""),
                    "description": "",
                    "price_amount": amount,
                    "price_currency": currency,
                    "url": item.get("itemWebUrl", ""),
                    "origin_country_code": item.get("itemLocation", {}).get("country"),
                    "extra": {
                        "search_term": term,
                        "ebay_item_id": item.get("itemId"),
                        "buying_options": buying_options,
                        "is_auction": is_auction,
                    },
                })

            time.sleep(REQUEST_DELAY_S)

    return raw_listings
