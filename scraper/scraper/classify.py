"""Klassificer annoncer som GODT KOEB / FAIR / OVERPRISET.

To metoder:
- classify(): statisk taerskeltabel fra config.yaml (bruges som bootstrap/fallback).
- classify_dynamic(): baseret paa percentiler af historiske priser i seen.db for
  samme model+generation -- falder tilbage til classify() naar der ikke er nok
  datapunkter endnu (kold start)."""
import datetime

# Enkeltenheds-ulempe: halvér par-taerskler + 10% oveni
SINGLE_UNIT_PENALTY = 1.10


def _threshold_key(model: str, gen: str) -> str | None:
    if model == "708a":
        if gen == "MK5":
            return "708a_mk5"
        return "708a_mk4"  # MK4 og aeldre/uoplyst grupperes med MK4-taerskler
    if model == "710a":
        if gen == "MK1":
            return None  # haandteres separat som beater
        if gen in ("MK2", "MK3"):
            return "710a_mk2_mk3"
        if gen == "MK4":
            return "710a_mk4"
        if gen == "MK5":
            return "710a_mk5"
        return "710a_mk2_mk3"  # uoplyst -> konservativt til den laveste gruppe
    if model == "910a":
        return "910a"
    return None


def classify(listing: dict, thresholds: dict, mk1_beater: dict) -> str:
    """listing skal indeholde: model, gen, quantity, price_per_unit_dkk."""
    model = listing.get("model")
    gen = listing.get("gen", "uoplyst")
    quantity = listing.get("quantity", 1)
    price_per_unit = listing.get("price_per_unit_dkk")

    if model is None or price_per_unit is None:
        return "UKENDT"

    # Par-pris til sammenligning med taerskler (som alle er par-priser)
    pair_price = price_per_unit * 2

    if model == "710a" and gen == "MK1":
        max_godt = mk1_beater["godt_koeb_max"]
        if quantity == 1:
            max_godt = (max_godt / 2) * SINGLE_UNIT_PENALTY
            compare_price = price_per_unit
        else:
            compare_price = pair_price
        return "GODT KØB" if compare_price <= max_godt else "OVERPRISET"

    key = _threshold_key(model, gen)
    if key is None or key not in thresholds:
        return "UKENDT"

    t = thresholds[key]
    godt_max = t["godt_koeb_max"]
    fair_min = t["fair_min"]
    fair_max = t["fair_max"]
    overpriced_min = t["overpriced_min"]

    if quantity == 1:
        godt_max = (godt_max / 2) * SINGLE_UNIT_PENALTY
        fair_min = (fair_min / 2) * SINGLE_UNIT_PENALTY
        fair_max = (fair_max / 2) * SINGLE_UNIT_PENALTY
        overpriced_min = (overpriced_min / 2) * SINGLE_UNIT_PENALTY
        compare_price = price_per_unit
    else:
        compare_price = pair_price

    if compare_price <= godt_max:
        return "GODT KØB"
    if compare_price >= overpriced_min:
        return "OVERPRISET"
    if fair_min <= compare_price <= fair_max:
        return "FAIR"
    return "FAIR"  # falder imellem grænserne uden at ramme et hul -> behandles som fair


def _percentile(sorted_values: list, pct: float) -> float:
    k = (len(sorted_values) - 1) * (pct / 100)
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def classify_dynamic(listing: dict, conn, thresholds: dict, mk1_beater: dict,
                      min_samples: int = 5, lookback_days: int = 180, exclude_id: str | None = None) -> tuple[str, str]:
    """Klassificerer ud fra percentiler af historiske priser (samme model+generation)
    i seen.db, naar der er nok datapunkter -- ellers statisk taerskeltabel som fallback.

    price_per_unit_dkk er allerede landed cost (fragt+told+moms inkluderet for
    ikke-EU-saelgere), saa sammenligningen er retfaerdig paa tvaers af kilder.

    exclude_id: udelader denne annonce fra sit eget historik-datasaet (bruges ved
    genberegning af allerede-gemte raekker, hvor annoncen selv allerede er i DB'en).

    NB (scraper-boilerplate-migrering, 2026-07-12): sammenligner mod kolonnen
    `item_key`, ikke `id` -- ren skema-navngivning der matcher scraper-core's
    konvention, ingen aendring i selve klassifikationslogikken.

    Returnerer (klassifikation, metode)."""
    model = listing.get("model")
    gen = listing.get("gen", "uoplyst")
    price_per_unit = listing.get("price_per_unit_dkk")

    if model is None or price_per_unit is None:
        return "UKENDT", "statisk"

    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=lookback_days)).isoformat()
    if exclude_id is not None:
        rows = conn.execute(
            "SELECT price_per_unit_dkk FROM listings "
            "WHERE model = ? AND gen = ? AND price_per_unit_dkk IS NOT NULL AND first_seen >= ? AND item_key != ?",
            (model, gen, cutoff, exclude_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT price_per_unit_dkk FROM listings "
            "WHERE model = ? AND gen = ? AND price_per_unit_dkk IS NOT NULL AND first_seen >= ?",
            (model, gen, cutoff),
        ).fetchall()
    historical = sorted(r[0] for r in rows if r[0] is not None)

    if len(historical) < min_samples:
        return classify(listing, thresholds, mk1_beater), "statisk (utilstraekkelig historik)"

    p25 = _percentile(historical, 25)
    p75 = _percentile(historical, 75)

    # Strengt < / > (ikke <= / >=): naar mange annoncer deler noejagtig samme pris
    # (den gaengse markedspris for stand/generation), kollapser p25 og p75 til samme
    # vaerdi -- med <=/>= ville HELE den gruppe fejlagtigt blive "GODT KØB". Strengt
    # ulighedstegn placerer den gaengse pris korrekt som FAIR, kun klart under/over
    # bliver GODT KØB/OVERPRISET.
    if price_per_unit < p25:
        return "GODT KØB", f"dynamisk (n={len(historical)})"
    if price_per_unit > p75:
        return "OVERPRISET", f"dynamisk (n={len(historical)})"
    return "FAIR", f"dynamisk (n={len(historical)})"


def priority_rank(listing: dict, priority_order: list) -> int:
    """Lavere tal = hoejere prioritet ved sortering af notifikationer."""
    model = listing.get("model")
    gen = listing.get("gen", "uoplyst")
    quantity = listing.get("quantity", 1)
    price_per_unit = listing.get("price_per_unit_dkk", float("inf"))
    pair_price = price_per_unit * 2 if quantity == 1 else price_per_unit * quantity

    for i, rule in enumerate(priority_order):
        rule_key = rule["key"]
        max_price = rule.get("max_price")

        if rule_key == "910a" and model == "910a":
            if max_price is None or pair_price <= max_price:
                return i
        elif rule_key == "710a_mk4" and model == "710a" and gen == "MK4":
            if max_price is None or pair_price <= max_price:
                return i
        elif rule_key == "710a_mk3" and model == "710a" and gen == "MK3":
            if max_price is None or pair_price <= max_price:
                return i
        elif rule_key == "708a" and model == "708a":
            return i

    return len(priority_order)  # laveste prioritet
