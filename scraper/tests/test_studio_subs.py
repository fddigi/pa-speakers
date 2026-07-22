"""F9-udvidelse (2026-07-14): model-genkendelse + tærskler for kategorien
"Studio sub" (Genelec 7050B/C/7040A/7350A, Dynaudio 9S/BM9S, SVS SB-1000/Pro).

Dækker de disambiguerings-regler der udløste "ingen matches" i produktion:
BM9S må ikke matches som 9S, SB-1000 uden Pro må ikke matches som Pro, og et
bogstavløst "Genelec 7050" skal markeres som ukendt revision i stedet for
enten B eller C.
"""
from __future__ import annotations

import pytest

from scraper import classify, normalize


def _t(godt_koeb_max, fair_min, fair_max, overpriced_min):
    return {
        "godt_koeb_max": godt_koeb_max, "fair_min": fair_min,
        "fair_max": fair_max, "overpriced_min": overpriced_min,
    }


STUDIO_SUB_THRESHOLDS = {
    "genelec_7050c": _t(4000, 4000, 5000, 5500),
    "genelec_7050_unknown": _t(2000, 2000, 2500, 2500),
    "genelec_7050b": _t(2000, 2000, 2500, 2500),
    "genelec_7040a": _t(2800, 2800, 3500, 4000),
    "genelec_7350a": _t(5000, 5000, 6500, 7000),
    "dynaudio_9s": _t(3500, 3500, 4500, 5000),
    "dynaudio_bm9s": _t(1750, 1750, 2250, 2500),
    "svs_sb1000_pro": _t(2800, 2800, 3500, 4000),
    "svs_sb1000_nonpro": _t(1800, 1800, 2300, 2800),
}


@pytest.mark.parametrize(
    "title, expected_model",
    [
        ("Genelec 7050C 8\" Powered Studio Subwoofer", "genelec_7050c"),
        ("Genelec 7050C uden grill - sælges", "genelec_7050c"),
        ("Dynaudio 9S aktiv sub, pænt brugt", "dynaudio_9s"),
        ("Dynaudio BM9S II - klassisk studiesub", "dynaudio_bm9s"),
        ("SVS SB-1000 Pro sælges, app-styret", "svs_sb1000_pro"),
        # Regression: uden "Pro" er det den ældre model, IKKE Pro-varianten.
        ("SVS SB-1000 velholdt, sort", "svs_sb1000_nonpro"),
        ("Genelec 7040A kompakt studiesub", "genelec_7040a"),
        ("Genelec 7350A med GLM-kit", "genelec_7350a"),
        ("Genelec 7350APM til salg", "genelec_7350a"),
        # Bogstavløs -- ukendt revision, IKKE automatisk B eller C.
        ("Genelec 7050 subwoofer, sælges hurtigt", "genelec_7050_unknown"),
        # Forvekslingsrisici der IKKE må matche noget studio-sub-mønster.
        ("Dynaudio Audience 9 højtalere, bogreol", None),
        ("Genelec 8250A GLM referencemonitor", None),
        ("Dynaudio Acoustics 18S True Bass Dual 9.5\" Active Subwoofer", None),
        # Udvidet søgefelt (2026-07-22, Fase 4-research).
        ("KEF KC62 subwoofer, som ny", "kef_kc62"),
        ("KRK S10.4 studio subwoofer", "krk_s10_4"),
        # Uden "KRK" i teksten skal "S10.4" IKKE matche (for uspecifikt alene).
        ("Aktiv subwoofer model S10.4, sælges", None),
        ("Adam Audio Sub10 Mk2 til salg", "adam_sub10"),
        ("Neumann KH 750 DSP subwoofer", "neumann_kh750"),
        ("Eve Audio TS108 aktiv sub", "eve_ts108"),
        ("Eve Audio TS107 kompakt sub", "eve_ts107"),
        ("Kali Audio WS-12 subwoofer", "kali_ws12"),
        ("RCF SUB 702-AS MK3 sælges", "rcf_sub702as"),
        ("RCF SUB 702-AS II, god stand", "rcf_sub702as"),
    ],
)
def test_extract_model_disambiguation(title, expected_model):
    assert normalize.extract_model(title) == expected_model


def test_bm9s_manual_is_filtered_as_accessory():
    """Fundet i produktion 2026-07-14: Reverbs søgning på "Dynaudio 9S" gav
    også løse BM9S-manualer (papir, ikke selve subben) som hits -- disse skal
    filtreres FØR normalisering, ellers klassificeres en $5-manual som en
    billig BM9S-subwoofer."""
    assert normalize.is_accessory_or_rental("Dynaudio Acoustics BM9S Owners Manual")
    assert normalize.is_accessory_or_rental("Dynaudio acoustics BM 9S Operating Guide")


@pytest.mark.parametrize(
    "model, price_per_unit, expected_classification",
    [
        ("genelec_7050c", 3900, "GODT KØB"),
        ("genelec_7050c", 4500, "FAIR"),
        ("genelec_7050c", 5600, "OVERPRISET"),
        # BM9S: halverede tærskler ift. 9S -- samme pris der er GODT KØB for
        # 9S skal være OVERPRISET for BM9S.
        ("dynaudio_9s", 3400, "GODT KØB"),
        ("dynaudio_bm9s", 3400, "OVERPRISET"),
        ("dynaudio_bm9s", 1700, "GODT KØB"),
        # SVS non-Pro: lavere tærskler end Pro.
        ("svs_sb1000_pro", 2700, "GODT KØB"),
        ("svs_sb1000_nonpro", 2900, "OVERPRISET"),
        ("svs_sb1000_nonpro", 1750, "GODT KØB"),
        # Ukendt revision bruger de konservative B-tærskler.
        ("genelec_7050_unknown", 1900, "GODT KØB"),
        ("genelec_7050_unknown", 2600, "OVERPRISET"),
    ],
)
def test_classify_studio_sub_thresholds(model, price_per_unit, expected_classification):
    listing = {
        "model": model, "gen": "uoplyst", "quantity": 1, "price_per_unit_dkk": price_per_unit,
    }
    assert classify.classify(listing, {}, {}, STUDIO_SUB_THRESHOLDS) == expected_classification


def test_classify_unknown_revision_note_via_classify_dynamic(tmp_path):
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE listings (item_key TEXT, model TEXT, gen TEXT, "
        "price_per_unit_dkk REAL, first_seen TEXT)"
    )
    listing = {
        "model": "genelec_7050_unknown", "gen": "uoplyst", "quantity": 1,
        "price_per_unit_dkk": 1900,
    }
    classification, method = classify.classify_dynamic(
        listing, conn, {}, {}, studio_sub_thresholds=STUDIO_SUB_THRESHOLDS,
    )
    assert classification == "GODT KØB"
    assert "ukendt revision" in method
