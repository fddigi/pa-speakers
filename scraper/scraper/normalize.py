"""Extract model/generation/quantity/price from raw listing text, convert to DKK."""
import re

MODEL_PATTERNS = [
    # (?!\d) i stedet for trailing \b: "710A"/"708A" osv. skrives ofte helt uden
    # separator foer modelbogstavet ("ART-710A-MK5"), saa der ikke er nogen ord-
    # graense mellem modelnummeret og "A". Fundet 2026-07-11 -- var hele tiden
    # maskeret for Reverb af boilerplate-beskrivelser der ogsaa naevner modellen
    # med mellemrum et andet sted, men ramte kilder med tom/kort beskrivelse.
    ("910a", re.compile(r"\bart\s*-?\s*910(?!\d)", re.I)),
    ("710a", re.compile(r"\bart\s*-?\s*710(?!\d)", re.I)),
    ("708a", re.compile(r"\bart\s*-?\s*708(?!\d)", re.I)),
    ("sub705", re.compile(r"\bsub\s*-?\s*705(?!\d)", re.I)),
    ("712", re.compile(r"\bart\s*-?\s*712(?!\d)", re.I)),
    # Yamaha DXR-serien -- ingen statiske taerskler endnu, se classify.py.
    # (?!\d) i stedet for trailing \b: mange titler skriver "DXR8MKII" helt uden
    # separator, saa der ikke er nogen ord-graense mellem cifferet og "MK" --
    # kun et EFTERFOELGENDE ciffer skal afvises (for at undgaa fx "DXR80").
    ("dxr8", re.compile(r"\bdxr\s*-?\s*8(?!\d)", re.I)),
    ("dxr10", re.compile(r"\bdxr\s*-?\s*10(?!\d)", re.I)),
    ("dxr12", re.compile(r"\bdxr\s*-?\s*12(?!\d)", re.I)),
    ("dxr15", re.compile(r"\bdxr\s*-?\s*15(?!\d)", re.I)),
]


# Leading (?<![a-zA-Z]) i stedet for \b: modelnummer+generation skrives ofte helt
# uden separator (fx "DXR8MKII"), saa der er ingen ord-graense mellem det sidste
# ciffer i modelnavnet og "MK" -- kun forudgaaende BOGSTAVER skal afvise matchet
# (for at undgaa fx "bookMKark").
GEN_PATTERN = re.compile(r"(?<![a-zA-Z])mk\s*-?\s*([1-5])\b", re.I)
# Nogle saelgere skriver generationen som romertal ("MKI", "MK V") i stedet for MK5 etc.
GEN_ROMAN_PATTERN = re.compile(r"(?<![a-zA-Z])mk\s*-?\s*(iv|iii|ii|i|v)\b", re.I)
ROMAN_TO_ARABIC = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5"}

PAIR_PATTERN = re.compile(r"\b(par|pair|paar|zwei|st(?:k|ück)?\.?\s*2|2\s*x|2x)\b", re.I)

# "Stückpreis" (tysk: pris PR. STYK) betyder den opgivne pris allerede er pr. enhed --
# ogsaa naar titlen samtidig naevner "2x" ("2 stk til salg hver til denne pris", ikke
# "denne pris daekker 2 stk"). Fundet 2026-07-08: to naesten identiske annoncer med
# samme raw pris, hvor kun den ene havde "2x" i titlen, blev fejlagtigt halveret.
PER_UNIT_PRICE_PATTERN = re.compile(
    # Kleinanzeigens listevisning afkorter lange titler (fx "Stückpreis" -> "Stückpre"),
    # saa "st(?:ü|u)ckpr" (uden krav om resten af ordet) fanger ogsaa trunkerede titler.
    r"\b(st(?:ü|u)ckpr\w*|pro\s*st(?:ü|u)ck|je\s*st(?:ü|u)ck|per\s*(?:unit|stk)|stykpris|per\s*styk)",
    re.I,
)

# Tilbehoer (cover/bracket/case/stand) og udlejning/soeges-annoncer er ikke salg af
# hele hoejttalere -- ekskluderes foer normalisering. Daekker tysk (Kleinanzeigen),
# svensk (Blocket), dansk og engelsk.
ACCESSORY_OR_RENTAL_PATTERN = re.compile(
    r"\b("
    r"covers?|cvr|brackets?|h-br|halterung(?:en)?|st[aä]nder|abdeckung(?:en)?|schutzh[üu]llen?|"
    r"taschen?|cases?|flightcases?|bags?|v[äa]skor?|fodral|skydd|hoes(?:en)?|"
    r"vermietung|verleih|miete[nt]?|uthyrning|hyra|hyr\b|rental|for\s*rent|til\s*leje|"
    r"s[øo]ges|sucht|gesucht|wanted|tausche"
    r")\b",
    re.I,
)


def is_accessory_or_rental(text: str) -> bool:
    """True hvis annoncen er tilbehoer, udlejning eller en soeges-annonce (ikke et salg)."""
    return bool(ACCESSORY_OR_RENTAL_PATTERN.search(text))


def extract_model(text: str) -> str | None:
    for key, pattern in MODEL_PATTERNS:
        if pattern.search(text):
            return key
    return None


def extract_gen(text: str) -> str:
    m = GEN_PATTERN.search(text)
    if m:
        return f"MK{m.group(1)}"
    m = GEN_ROMAN_PATTERN.search(text)
    if m:
        return f"MK{ROMAN_TO_ARABIC[m.group(1).lower()]}"
    return "uoplyst"


def extract_quantity(text: str) -> int:
    if PER_UNIT_PRICE_PATTERN.search(text):
        return 1
    return 2 if PAIR_PATTERN.search(text) else 1


def to_dkk(amount: float, currency: str, rates: dict) -> float:
    currency = currency.upper()
    if currency == "DKK":
        return amount
    if currency == "EUR":
        return amount * rates["eur_dkk"]
    if currency == "SEK":
        return amount * rates["sek_dkk"]
    if currency == "USD":
        return amount * rates["usd_dkk"]
    raise ValueError(f"Ukendt valuta: {currency}")


def compute_landed_price_dkk(price_dkk: float, origin_country_code: str | None, import_costs: dict) -> tuple[float, float]:
    """Beregner landed cost (reel slutpris inkl. fragt+told+moms) for saelgere udenfor EU.

    Returnerer (landed_price_dkk, shipping_customs_dkk). For EU-saelgere (eller ukendt
    oprindelse) er landed_price_dkk == price_dkk (ingen graenseomkostninger modelleret --
    kun graenseoverskridende ikke-EU-import giver de store, ofte overraskende tillaeg).
    """
    eu_codes = set(import_costs.get("eu_country_codes", []))
    if origin_country_code is None or origin_country_code.upper() in eu_codes:
        return price_dkk, 0.0

    shipping_dkk = import_costs["default_shipping_dkk"]
    eur_dkk = import_costs.get("_eur_dkk_rate", 1.0)
    customs_value_dkk = price_dkk + shipping_dkk
    duty_threshold_dkk = import_costs["duty_threshold_eur"] * eur_dkk

    duty_dkk = customs_value_dkk * (import_costs["duty_pct"] / 100) if customs_value_dkk > duty_threshold_dkk else 0.0
    vat_dkk = (customs_value_dkk + duty_dkk) * (import_costs["vat_pct"] / 100)

    shipping_customs_dkk = shipping_dkk + duty_dkk + vat_dkk
    return price_dkk + shipping_customs_dkk, shipping_customs_dkk


def normalize_listing(*, source: str, title: str, description: str, price_amount: float,
                       price_currency: str, url: str, rates: dict, extra: dict | None = None,
                       origin_country_code: str | None = None, import_costs: dict | None = None) -> dict:
    """Bygger et normaliseret listing-dict klar til dedup/klassifikation.

    Antal (par vs. enkelt) udledes KUN fra titlen, ikke beskrivelsen -- beskrivelser
    (isaer Reverbs boilerplate produkttekst) indeholder ofte "2 x" i teknisk kontekst
    (f.eks. "Hardware: 2 x M10" monteringsbolte), som fejlagtigt blev tolket som "par".
    Saelgere angiver paalideligt antal i titlen ("Pair", "(PAIR)", "2x ...").

    Model/generation prioriterer OGSAA titlen -- falder kun tilbage til
    beskrivelsen hvis titlen intet match har. Fundet 2026-07-11: en Yamaha
    DXR15-annonces beskrivelse naevnte OGSAA "DXR12mkII" i generisk produkttekst
    (sammenligner hele DXR-serien), hvilket fik modellen til fejlagtigt at blive
    laest som DXR12 i stedet for DXR15 (titlens faktiske model).
    """
    text = f"{title} {description}"
    model = extract_model(title) or extract_model(text)
    gen = extract_gen(title) or extract_gen(text)
    quantity = extract_quantity(title)
    price_dkk = to_dkk(price_amount, price_currency, rates)

    if import_costs is not None:
        import_costs = {**import_costs, "_eur_dkk_rate": rates["eur_dkk"]}
        landed_price_dkk, shipping_customs_dkk = compute_landed_price_dkk(price_dkk, origin_country_code, import_costs)
    else:
        landed_price_dkk, shipping_customs_dkk = price_dkk, 0.0

    price_per_unit_dkk = landed_price_dkk / quantity if quantity else landed_price_dkk

    return {
        "source": source,
        "title": title,
        "model": model,
        "gen": gen,
        "quantity": quantity,
        "price_dkk": price_dkk,
        "landed_price_dkk": landed_price_dkk,
        "shipping_customs_dkk": shipping_customs_dkk,
        "origin_country": origin_country_code,
        "price_per_unit_dkk": price_per_unit_dkk,
        "url": url,
        "raw": extra or {},
    }
