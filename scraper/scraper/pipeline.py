"""Adapter layer connecting PA SPEAKERS' original business logic (normalize.py,
classify.py, sources/*.py's fetch() functions -- all ported unchanged) to
scraper-core's delta-sync pattern (LocalStore.upsert_if_changed + the Turso
outbox). One shared `run_source()` reused by all five sources; only `fetch()`
itself differs per source, exactly like the original monitor.py's per-source
try/except loop.
"""
from __future__ import annotations

import datetime
import json
import logging

from scraper_core.local_db import LocalStore
from scraper_core.watchdog import SourceTimeoutError, run_with_timeout

from . import classify, normalize

logger = logging.getLogger(__name__)

TARGET_TABLE = "listings"

# Kept as LOCAL_SCHEMA/TURSO_SCHEMA (identical for now) on purpose, matching the
# dummy example's convention -- local and Turso are allowed to diverge later.
LOCAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    item_key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT,
    model TEXT,
    gen TEXT,
    quantity INTEGER,
    price_dkk REAL,
    landed_price_dkk REAL,
    shipping_customs_dkk REAL,
    origin_country TEXT,
    price_per_unit_dkk REAL,
    classification TEXT,
    classification_method TEXT,
    url TEXT,
    first_seen TEXT NOT NULL,
    raw_json TEXT
);
"""
TURSO_SCHEMA = LOCAL_SCHEMA

_INSERT_SQL = """
INSERT INTO listings (item_key, source, title, model, gen, quantity, price_dkk,
    landed_price_dkk, shipping_customs_dkk, origin_country, price_per_unit_dkk,
    classification, classification_method, url, first_seen, raw_json)
VALUES (:item_key, :source, :title, :model, :gen, :quantity, :price_dkk,
    :landed_price_dkk, :shipping_customs_dkk, :origin_country, :price_per_unit_dkk,
    :classification, :classification_method, :url, :first_seen, :raw_json)
ON CONFLICT(item_key) DO UPDATE SET
    title = excluded.title, model = excluded.model, gen = excluded.gen,
    quantity = excluded.quantity, price_dkk = excluded.price_dkk,
    landed_price_dkk = excluded.landed_price_dkk,
    shipping_customs_dkk = excluded.shipping_customs_dkk,
    origin_country = excluded.origin_country,
    price_per_unit_dkk = excluded.price_per_unit_dkk,
    classification = excluded.classification,
    classification_method = excluded.classification_method,
    url = excluded.url, raw_json = excluded.raw_json
"""


def make_item_key(
    source: str, url: str | None, title: str | None = None, price_dkk: float | None = None
) -> str:
    """Ported unchanged from PA SPEAKERS' db.py:make_id()."""
    import hashlib

    basis = f"{source}|{url}" if url else f"{source}|{title}|{price_dkk}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def run_source(
    store: LocalStore,
    source_name: str,
    fetch_fn,
    config: dict,
    dry_run: bool = False,
    fetch_timeout_seconds: float = 300,
) -> tuple[int, int, list[dict]]:
    """Runs one source's fetch() -> normalize -> classify -> upsert_if_changed.

    Isolated try/except per source, matching the original monitor.py: one source's
    failure must never crash the others or the rest of the run. Returns
    (raw_count, changed_count, price_drop_events) -- see F5 (BACKLOG.md): the
    third element is a list of dicts for `price_history.py` to sync, one per
    already-known listing whose price_per_unit_dkk just DECREASED (a genuine
    price drop, not e.g. a quantity change or first sighting).

    fetch_fn() itself already gets bounded per-request timeouts (Playwright
    page.goto/wait_for_selector, requests' own timeout=) - fetch_timeout_seconds
    is a wall-clock BACKSTOP on top of those, for the whole fetch() call across
    all its search terms/pages, via scraper_core.watchdog.run_with_timeout().
    NOTE (see SCRAPING_LESSONS.md / scraper-core's own watchdog.py docstring):
    this only bounds how long THIS FUNCTION waits - it cannot forcibly kill a
    truly stuck fetch_fn() (confirmed: CPython's ThreadPoolExecutor registers
    an atexit hook that still joins the abandoned thread at interpreter exit,
    so a genuinely infinite hang - a blocked call with no timeout of its own -
    will still prevent the whole process from exiting, same as before this
    was added). It DOES bound a source that is merely slow-but-finite (e.g.
    stuck in retries for several minutes), so it can't delay the sources that
    run after it in the same sequential loop.
    """
    store.executescript(LOCAL_SCHEMA)
    raw_count = 0
    changed = 0
    price_drop_events: list[dict] = []

    try:
        raw_listings = run_with_timeout(
            lambda: fetch_fn(config, dry_run=dry_run),
            timeout_seconds=fetch_timeout_seconds,
            source_name=source_name,
        )
        raw_count = len(raw_listings)
        rates = config["currency"]

        for raw in raw_listings:
            # Titel-kun tilbehoers-/udlejningsfilter -- se normalize.py's docstring.
            if normalize.is_accessory_or_rental(raw.get("title", "")):
                continue

            listing = normalize.normalize_listing(
                source=source_name,
                title=raw.get("title", ""),
                description=raw.get("description", ""),
                price_amount=raw["price_amount"],
                price_currency=raw["price_currency"],
                url=raw.get("url", ""),
                rates=rates,
                extra=raw.get("extra"),
                origin_country_code=raw.get("origin_country_code"),
                import_costs=config.get("import_costs"),
            )
            item_key = make_item_key(
                source_name, listing.get("url"), listing.get("title"), listing.get("price_dkk")
            )
            first_seen = datetime.datetime.now(datetime.UTC).isoformat()

            # F5: fanget FØR upsert'en nedenfor kan overskrive den - eneste sted
            # den gamle pris/klassifikation stadig er tilgængelig, for at kunne
            # opdage et reelt PRISFALD (ikke bare en hvilken som helst ændring).
            previous_row = store.connection.execute(
                "SELECT price_per_unit_dkk, classification FROM listings WHERE item_key = ?",
                (item_key,),
            ).fetchone()

            # exclude_id=item_key: without this, an ad that matches multiple search
            # terms gets processed more than once per run, and its SECOND pass would
            # see its own just-inserted row in the historical percentile query,
            # possibly flipping its own classification and triggering a spurious
            # re-sync. Found 2026-07-12 via a Blocket ad queued 3x in one run for
            # what should have been 1 insert + 0 real changes.
            classification, method = classify.classify_dynamic(
                listing, store.connection, config["thresholds"], config["mk1_beater"],
                exclude_id=item_key,
            )

            payload = {
                "item_key": item_key,
                "source": source_name,
                "title": listing.get("title"),
                "model": listing.get("model"),
                "gen": listing.get("gen"),
                "quantity": listing.get("quantity"),
                "price_dkk": listing.get("price_dkk"),
                "landed_price_dkk": listing.get("landed_price_dkk"),
                "shipping_customs_dkk": listing.get("shipping_customs_dkk"),
                "origin_country": listing.get("origin_country"),
                "price_per_unit_dkk": listing.get("price_per_unit_dkk"),
                "classification": classification,
                "classification_method": method,
                "url": listing.get("url"),
                "first_seen": first_seen,
                "raw_json": json.dumps(listing.get("raw", {}), default=str),
            }

            is_new_or_changed = store.upsert_if_changed(
                source=source_name,
                item_key=item_key,
                payload=payload,
                target_table=TARGET_TABLE,
                # first_seen is set fresh every run (same reasoning as the dummy
                # example's scraped_at exclusion). raw_json is ALSO excluded: it
                # carries which search_term happened to match this ad, which
                # legitimately differs when the same real ad matches more than one
                # of our search terms -- found 2026-07-12 via a Blocket ad that
                # matched 3 terms and was queued 3x for what should have been one
                # insert + zero real content changes, since nothing about the ad
                # itself (title/price/model) had actually changed.
                hash_payload={
                    k: v for k, v in payload.items() if k not in ("first_seen", "raw_json")
                },
            )
            if not is_new_or_changed:
                continue

            store.connection.execute(_INSERT_SQL, payload)
            store.connection.commit()
            changed += 1

            # F5: kun en reel PRISNEDSÆTTELSE tæller (strengt <, ikke bare "ændret") -
            # udelukker fx en ren quantity- eller titel-rettelse der tilfældigvis også
            # trigger is_new_or_changed uden at prisen faldt.
            old_price = previous_row["price_per_unit_dkk"] if previous_row is not None else None
            new_price = payload["price_per_unit_dkk"]
            if old_price is not None and new_price is not None and new_price < old_price:
                price_drop_events.append({
                    "item_key": item_key,
                    "old_price_per_unit_dkk": old_price,
                    "new_price_per_unit_dkk": new_price,
                    "pct_change": round((new_price - old_price) / old_price * 100, 1),
                    "old_classification": previous_row["classification"],
                    "new_classification": classification,
                    "observed_at": first_seen,
                })

        logger.info("%s: %d raw, %d new/changed", source_name, raw_count, changed)
    except SourceTimeoutError:
        # Already logged by run_with_timeout() itself (logger.warning) - avoid a
        # duplicate/misleading logger.exception() stack trace pointing at the
        # watchdog's own raise site instead of the actual stuck fetch_fn() call.
        pass
    except Exception:
        logger.exception("%s: source failed, skipping - other sources unaffected", source_name)

    return raw_count, changed, price_drop_events
