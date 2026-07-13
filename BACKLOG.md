# BACKLOG — prioriteret med WSJF

> Formateret til almindelig `cat`/`watch` i et smalt terminalvindue — INGEN
> markdown-tabeller, kun fastbredde-tekst i kodeblokke. Hold linjer under ~70
> tegn ved redigering, så det forbliver læseligt i en delt tmux-pane.
>
> WSJF = Cost of Delay / Job Size. CoD = BV + TC + RR (hver 1–10, 10=størst).
> Size = jobstørrelse 1–10 (10=størst). Re-scores når en feature leveres.
> Detaljerede specs pr. feature: se FEATURES.md

## Prioriteret rækkefølge (næste øverst)

```
1. F9   Kategorisering af søgninger  WSJF 1.4  TODO
```

F6, F8, F7, F11, F10, F5 og F4 er leveret — se "Leveret" nedenfor. F4 er
dog INAKTIV indtil brugeren selv sætter EBAY_APP_ID/EBAY_CERT_ID (se
README.md).

## Scoring-detaljer

```
F9  Kategorisering af søgninger
    BV 5  TC 2  RR 3  CoD 10  Size 7  WSJF 1.4
    Cross-cutting (scraper + worker + frontend + TO skemamigreringer).
    v1 er KUN organisering + tagging; percentil-klassifikationen er
    allerede model+gen-partitioneret og røres ikke. Reelt synth-/
    studie-scope (nye model-regex'er, nye tærskler, kilde-routing,
    Thomann-kategori-URL'er) er en SEPARAT epic på størrelse med F4+
    -- derfor højt Size og lav BV for selve v1-skiven. TC lav.
```

## Begrundelser (kort)

- F6 øverst: ren efterbehandling af data vi allerede har, adresserer
  direkte slutmålet (910A-par <=6.500). Lavt Size.
- F8 højt trods "kun UI": krydser 5.5 fordi den er billig (Size 2) og
  fjerner en reel mobil-blocker -- sortering findes men er utilgængelig
  på telefon. Ingen backend-risiko.
- F11 over F10: nye kilder tjener acquisition-målet direkte, mens
  drilldown er forklarende polish. Begge er spikes -- konklusionen kan
  være "byg ikke" (specielt Facebook i F11).
- F5 lavt trods god værdi: kræver genbesøgs-logik pr. kilde + ny tabel.
- F4 næstsidst: størst job (API-nøgle, OAuth, nyt modul), overlapper
  delvist med F1/F2 (samme tyske/nordiske brugtmarked).
- F9 sidst: største ikke-F4-job og en scope-fælde. v1 leverer kun
  overblik/tagging; den værdi brugeren egentlig vil have (finde synth-
  kup) ligger i en ubygget v2. Lav WSJF afspejler at prisen er høj og
  den leverede værdi i v1-skiven er begrænset.

## Leveret

```
2026-07-07  Grundsystem: 4 kilder, dedup, statisk klassifikation
2026-07-08  Tilbehørs-/udlejningsfilter (titel-baseret)
2026-07-08  Landed cost (fragt+told+moms) for ikke-EU
2026-07-08  Dynamisk klassifikation (25./75. percentil)
2026-07-08  dashboard.html + recompute.py
2026-07-08  launchd-automatisering (F7, delvist) -- hver 2. time
2026-07-08  F3: Reverb condition=used-filter (170->24/kørsel)
2026-07-08  F1: Kleinanzeigen-paginering + session-fix (8->47 unikke)
2026-07-08  Bugfix: Thomann 0-kr GODT KØB ved fejlet prishentning
2026-07-08  Bugfix: quantity manglede "Paar"/"zwei"/"Stückpreis"
2026-07-10  Manuel rettelse: Blocket 24024342 (par kun paa foto)
2026-07-10  F2: DBA.dk som ny kilde (Schibsted-platform, ikke RSS)
2026-07-11  playwright.headless: true (ingen synlige Chrome-vinduer)
2026-07-11  Yamaha DXR8/10/12/15 tilfoejet som nye modeller
2026-07-11  Bugfix: model-regex fejlede paa "710A"/"DXR8MKII" u. mellemrum
2026-07-11  Bugfix: beskrivelse kunne overtrumfe titlens rigtige model
2026-07-13  F6: Blandet par-alarm (mixed_pairs, /api/mixed-pairs)
2026-07-13  F8: Mobil-sortering (<select id="mobile-sort">)
2026-07-13  F7: Per-kilde-kadence, resten (source_state, --source)
2026-07-13  F11: Spike flere kilder -- Gearloop bygget, Thomann
            nypris-reference bygget (kun 910A kortlagt), Facebook
            bevidst udelukket (se FEATURES.md F11)
2026-07-13  F10: Spike prishistorik -- ingen ægte tidsserie mulig,
            byg klikbart klassifikations-drilldown-panel (tekst,
            ikke graf), GET /api/listings/:itemKey/context
2026-07-13  F5: Prisfalds-detektion -- append-only price_history,
            "↓ X% (Y dage siden)" vist inline i Pris/enhed-kolonnen
2026-07-13  F4: eBay.de som kilde -- sources/ebay.py bygget og
            testet (graceful skip uden nøgle, auktioner tvinges
            UKENDT), kræver brugerens egen EBAY_APP_ID/CERT_ID
```

Se git-historik/tidligere samtale for fulde detaljer pr. leverance.
Kort version af de to største fund:

- **Kleinanzeigen session-degradering:** siden returnerer stille 0
  resultater fra 2. forespørgsel i samme browser-context (ingen
  bot-wall-tekst). Rettet med frisk context pr. forespørgsel.
- **Blocket 24024342:** hverken titel eller beskrivelse nævnte "par",
  kun annoncefotoet viste 2 højttalere — rettet manuelt, ikke via regex.
- **F2 (DBA.dk):** intet RSS længere — dba.dk omdirigerer til
  /recommerce/forsale/search, samme Schibsted-platform/markup som
  Blocket ("sf-search-ad"). `sources/dba.py` genbruger derfor
  Blockets selectors 1:1. Sparsomt RCF-udbud på DBA lige nu (0-3 pr.
  term) — men kilden virker og er klar til når udbuddet stiger.
- **Model-regex uden mellemrum:** "710A"/"DXR8MKII" osv. skrives ofte
  helt uden separator — ramte kun kilder med kort/tom beskrivelse
  (Reverbs lange boilerplate maskerede fejlen indtil nu). Rettet med
  `(?!\d)` i stedet for `\b` som afgrænsning.
- **Beskrivelse kunne overtrumfe titlen:** en Yamaha DXR15-annonces
  beskrivelse nævnte OGSÅ "DXR12mkII" (sammenligner hele serien),
  hvilket fejlagtigt overskrev titlens korrekte DXR15. Model/gen
  prioriterer nu titlen, falder kun tilbage til beskrivelsen hvis
  titlen intet match har.

## Kendt strukturel begrænsning

Systemet er rent tekstbaseret (titel+beskrivelse), analyserer ALDRIG
billeder. Antal der kun fremgår af fotos kan ikke opdages automatisk.
Ikke bygget (kræver vision-API pr. annonce); spot-tjek manuelt ved tvivl.

## Vedligeholdelse af denne fil

1. Ny idé → tilføj som næste F-nummer i FEATURES.md, scor den her.
2. Påbegyndt → Status: IN PROGRESS. Leveret → flyt til "Leveret".
3. Re-scor eksisterende rækker når forudsætninger ændrer sig. Notér
   væsentlige re-scoringer i git-historik/commit-besked.
4. Hold kodeblok-linjerne under ~70 tegn (se note øverst i filen).
