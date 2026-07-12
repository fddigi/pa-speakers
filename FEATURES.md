# FEATURES — specs til udvikleragenten

> Vedligeholdes sammen med [BACKLOG.md](BACKLOG.md) (prioritering + status dér, specs her).
> Konventioner: hver feature har Problem / Design / Berørte filer / Acceptkriterier / Risici.
> Designforslagene er et bud — afvig gerne, men dokumentér hvorfor i PR/commit.
> Arkitekturprincipper der SKAL overholdes: (1) én kildes fejl vælter aldrig de andre,
> (2) --dry-run skriver ALDRIG til DB (heller ikke kildernes egne tabeller),
> (3) alt der påvirker klassifikation skal kunne genberegnes via recompute.py,
> (4) quantity/tilbehørsfilter kigger KUN på titler, aldrig beskrivelser.

---

## F1: Dybere Kleinanzeigen-dækning [LEVERET 2026-07-08]

**Faktisk implementering:** som designet nedenfor. `_build_search_url(term, page_num)`
bruger `/s-{query}/k0` for side 1, `/s-seite:{n}/{query}/k0` for side 2+ — bekræftet
mod live site (pagination-nav'ens `<a href>` for en flersidet testsøgning). Config
omdøbt: `max_pages_per_term: 3`, `max_pages_total: 20` (nødbremse på tværs af alle
termer). Bot-wall stopper hele kilden (nested break via `bot_wall_hit`-flag); 0 kort
på en side stopper kun den term og fortsætter til næste. Samme config-nøgler
opdateret i `sources/blocket.py` (kun navne, ikke selve paginerings-logikken, jf.
det oprindelige forslag).
**Live-observation:** en bred testterm ("lautsprecher") gav 26 rå annoncer over
flere sider — pagineringen virker. Vores faktiske RCF-søgetermer var reelt udtømte
efter side 1 for 4 af 5 termer på testdagen (0 kort = korrekt stop-signal, ikke en
fejl) — dybden af Kleinanzeigen-dækningen afhænger derfor af hvor meget der reelt
er til salg lige nu, ikke af kode-begrænsningen som før.

**Problem:** `max_pages_per_run: 5` er et samlet budget på tværs af 5 søgetermer →
vi henter kun side 1 pr. søgning og paginerer aldrig. Tyskland er hovedkilden til
brugte 910A/710A uden told; vi ser en brøkdel af markedet.

**Design:**
- Config: erstat `playwright.max_pages_per_run` med `playwright.max_pages_per_term`
  (default 3). Behold et samlet loft `playwright.max_pages_total` (default 20) som
  nødbremse.
- Paginering: Kleinanzeigens søge-URL'er har formen `/s-<query>/k0` for side 1 og
  `/s-seite:<n>/<query>/k0` for side n — verificér selektoren mod live site før brug.
  Stop pagineringen for en term når en side giver 0 kort (sidste side).
- Behold randomiseret 3–8s delay MELLEM ALLE sidehentninger (også paginering).
- Bot-wall-detektion pr. side som nu: log + afbryd hele kilden for denne kørsel
  (ikke kun termen) — mere aggressiv hentning øger blokeringsrisiko, og en blokeret
  session skal ikke fortsætte med at hamre.

**Berørte filer:** `config.yaml`, `sources/kleinanzeigen.py` (samme mønster
forberedes i `sources/blocket.py`, men Blocket har få hits — lav evt. kun config-
nøglerne der).

**Acceptkriterier:**
- En kørsel henter op til 3 sider pr. term og logger antal sider/term.
- 0-korts-side afslutter termen uden fejl.
- Simuleret bot-wall (mock/manuel test) afbryder kilden uden at vælte de andre.
- `--dry-run` uændret semantik.

**Risici:** Øget blokeringsrisiko (mitigeret af delay + total-loft). Kleinanzeigens
URL-format for paginering kan ændre sig — verificér empirisk først.

---

## F2: DBA/Guloggratis RSS-poll [LEVERET 2026-07-10 (kun DBA)]

**Faktisk implementering:** Spike bekræftede at dba.dk IKKE har RSS
længere -- alle søgninger omdirigerer (301) til
`/recommerce/forsale/search?q=...`, samme Schibsted-platform som
Blocket.se. Verificeret identisk markup (`article.sf-search-ad`,
`h2`-titel, `a.sf-search-ad-link`, `.font-bold`-pris) -- `sources/dba.py`
er derfor Blockets Playwright-tilgang med DKK i stedet for SEK (ingen
valutakonvertering) og `origin_country_code="DK"`. Live-testet: virker
korrekt, men sparsomt RCF-udbud lige nu (0-3 hits/term). Guloggratis
IKKE undersøgt endnu -- separat spike krævet, se Design nedenfor.

**Problem:** DK-annoncer (ingen fragt/told, afhentning mulig) er helt fraværende.
Oprindelig spec: byg kun hvis trivielt (RSS/HTML-poll).

**Design:**
- FØRSTE SKRIDT (spike, max 1 time): verificér om dba.dk stadig udstiller RSS på
  søgninger (historisk: `https://www.dba.dk/soeg/?soeg=<query>&format=rss` eller
  RSS-link i søgeresultatets HTML). Hvis intet RSS: prøv simpel HTML-GET og vurdér
  om markup er server-renderet og stabil. Hvis begge fejler → luk featuren som
  "ikke trivielt", notér i BACKLOG.md, og læn os på DBA's egne mail-alerts (som
  brugeren allerede har).
- Hvis RSS findes: nyt modul `sources/dba.py` efter samme kontrakt som de andre
  (`fetch(config, dry_run=False) -> list[raw]`), `feedparser` ELLER stdlib
  `xml.etree` (foretræk stdlib, undgå ny dependency). `origin_country_code="DK"`.
- Config: `dba_search_urls:` liste (én RSS-URL pr. søgeterm), on/off i `sources:`.
- Guloggratis: samme spike; hvis kun én af de to er triviel, byg kun den.

**Berørte filer:** `sources/dba.py` (ny), `config.yaml`, `monitor.py`
(SOURCE_MODULES), `README.md`.

**Acceptkriterier:**
- Spike-konklusion dokumenteret (også ved nej).
- Ved ja: DK-annoncer i seen.db med korrekt DKK-pris og origin DK; dedup virker
  (kørsel 2 giver 0 nye); kildefejl isoleret.

**Risici:** RSS kan være nedlagt (spike afklarer billigt). DBA kan have
bot-beskyttelse på HTML-fallback.

---

## F3: Reverb EU-/brugt-filter [LEVERET 2026-07-08]

**Faktisk implementering (v1-forslaget nedenfor, ikke item_region-varianten):**
`condition=used`-param tilføjet i `sources/reverb.py` (170→24 raa/kørsel). Origin-
udelukkelse blev IKKE aktiveret (`exclude_origin_countries: []` i config) — testet
med US ekskluderet, men Reverb havde 0 bekræftede EU-brugte annoncer for vores
modeller den dag, hvilket ville have reduceret Reverb til 3 annoncer. Landed cost
(fragt+told+moms) lægges allerede korrekt oveni US-priser, og dynamisk percentil-
klassifikation håndterer prisforskellen — så US beholdes. Historisk purge kørt via
`recompute.py`: 78/100 gemte Reverb-rækker var 'brand-new' (samme forhandlerstøj),
nu fjernet fra klassifikations-historikken.
**Hvis du re-kører denne feature-spec:** genovervej `exclude_origin_countries` —
resultatet er dato-afhængigt, da udbuddet af brugte EU-annoncer på Reverb varierer.

**Problem:** 81/100 Reverb-rækker er amerikanske forhandlere med nyt/open-box til
næsten-nypris. De (a) drukner boardet i FAIR/OVERPRISET og (b) forurener
percentilerne i den dynamiske klassifikation, så reelle kup fejlklassificeres.

**Design:**
- Reverb API'et understøtter `item_region=<ISO-landekode>` og `condition=used`
  (empirisk verificeret 2026-07-08: 59 → 8 hits for brugte 910A).
- Config, ny sektion:
  ```yaml
  reverb:
    item_regions: ["DE", "DK", "SE", "IT", "NL", "BE", "FR", "AT", "PL"]
    conditions: ["used"]          # tom liste = alle conditions
    include_unfiltered_pass: false  # true = kør OGSÅ en ufiltreret søgning (støjfuld)
  ```
- `sources/reverb.py`: én søgning pr. (term × region) med condition-param.
  NB: flere regioner = flere API-kald; behold 1s delay. Med 5 termer × 9 regioner
  er det op mod 45 kald/kørsel — overvej at samle til 2–3 kørsler med
  `item_region` udeladt + post-filter på origin, hvis rate limiting rammer.
  Simplere alternativ (anbefalet som v1): behold nuværende søgning UDEN
  item_region, tilføj kun `condition=used`, og post-filtrér på inferred origin
  (EU-koder beholdes, US droppes) FØR normalisering. Det er ét config-flag +
  3 linjer kode og løser 90% af problemet.
- Gem `condition` (slug fra API'et) i raw_json + evt. egen kolonne til dashboardet.
- Efter udrulning: kør `recompute.py` — percentilerne skal genberegnes på det
  rensede datasæt. Overvej at purge eksisterende US-forhandler-rækker (eller markér
  dem, så classify_dynamic kan ekskludere dem fra historikken).

**Berørte filer:** `sources/reverb.py`, `config.yaml`, evt. `db.py` (condition-
kolonne), `recompute.py` (purge/markér-logik), `dashboard.py` (vis condition).

**Acceptkriterier:**
- Med `conditions: ["used"]` indeholder en frisk kørsel ingen brand-new Reverb-rækker.
- US-rækker optræder ikke (eller er markeret ekskluderet fra percentil-historik).
- recompute.py efterlader konsistent klassifikation på det rensede datasæt.

**Risici:** For hårdt filter kan skjule reelle EU-forhandler-B-stock-kup → derfor
config-styret, ikke hårdkodet. `item_region`-semantik er "ships from" — verificér
mod et par kendte annoncer.

---

## F4: eBay.de som kilde

**Problem:** Stort tysk brugtmarked (inkl. auktioner der kan ende under markedspris)
er udækket.

**Design:**
- eBay Browse API (officielt, gratis nøgle via developer.ebay.com — BRUGER-
  AFHÆNGIGHED: nøgle skal oprettes af brugeren).
- OAuth2 client-credentials flow (application token, ingen bruger-login).
  Token caches i memory pr. kørsel.
- Nyt modul `sources/ebay.py`: Browse API `item_summary/search` med
  `marketplace_id=EBAY_DE`, query pr. søgeterm, `filter=conditions:{USED}`.
  Priser kommer i EUR → eksisterende to_dkk. `origin_country_code` fra
  `itemLocation.country`.
- Auktioner: `buyingOptions` felt — gem i raw_json; auktioner med lav nuværende
  pris er IKKE en landed pris → markér tydeligt i notify/dashboard ("auktion,
  slutpris ukendt") i stedet for at klassificere dem som GODT KØB.
- Config: `ebay:` sektion med `app_id`/`cert_id` (læses fra env-variabler, IKKE
  klartekst i config.yaml — den ligger i git).

**Berørte filer:** `sources/ebay.py` (ny), `config.yaml`, `monitor.py`,
`README.md` (nøgle-opsætning), evt. `.env`-håndtering.

**Acceptkriterier:**
- Kørsel med gyldig nøgle giver eBay.de-annoncer med korrekt EUR→DKK og origin.
- Manglende/ugyldig nøgle: kilden logger og springes over (som bot-wall-mønstret).
- Auktioner klassificeres aldrig GODT KØB alene på nuværende bud.

**Risici:** Størst job; nøgle-afhængighed; eBay rate limits (5000 kald/dag på
gratis tier — rigeligt).

---

## F5: Prisfalds-detektion

**Problem:** Dedup-nøglen er source+url, så en kendt annonce der sætter prisen ned
genopdages aldrig — selvom prisfald netop er dét, der bringer annoncer ind i
prisrammen (typisk efter 2–4 ugers liggetid).

**Design:**
- Ny tabel `price_history(listing_id, price_dkk, landed_price_dkk, observed_at)`
  (append-only). Ved HVER kørsel: for hver hentet annonce der allerede findes i
  listings, sammenlign ny pris med senest gemte; ved ændring: append til
  price_history + opdatér listings-rækken (pris, landed, klassifikation).
- Genklassificér ved prisændring; hvis klassifikationen KRYDSER en grænse
  (fx FAIR → GODT KØB), medtag annoncen i notify som "PRISFALD" med gammel/ny pris.
- Kilderne henter allerede alle annoncer pr. kørsel (Reverb/Kleinanzeigen/Blocket
  søger bredt) — der skal IKKE bygges separat genbesøgs-crawler; ændringen ligger
  i monitor.py's upsert-flow (i dag INSERT OR IGNORE → skal blive
  "insert eller opdatér-ved-prisændring").
- `db.upsert_listing` får ny semantik — behold first_seen uændret, tilføj
  `last_seen` kolonne (opdateres hver gang annoncen ses). Det giver også gratis
  "forsvundet fra markedet"-signal senere.
- Dashboard: vis seneste prisændring (fx "↓ 12% for 8 dage siden").

**Berørte filer:** `db.py` (tabel + upsert-semantik), `monitor.py`, `notify.py`
(PRISFALD-sektion), `dashboard.py`, `recompute.py` (price_history skal ikke
genberegnes — kun observationer).

**Acceptkriterier:**
- Samme annonce med ny pris → price_history-række + opdateret klassifikation.
- Grænsekrydsende prisfald → notifikation med gammel/ny pris.
- first_seen er stabil; --dry-run skriver intet.

**Risici:** upsert-semantikændring rammer alt — kræver omhyggelig test af
idempotens (kørsel 2 uden prisændringer = 0 notifikationer, 0 nye history-rækker).

---

## F6: "Blandet par"-alarm

**Problem:** 105/112 annoncer er enkeltenheder — men målet er et PAR (fx 910A
≤6.500). To billige singler af samme model+gen kan tilsammen opfylde målet
(aktuelt bedste: 3.424 + 3.730 = 7.154), og det ser ingen i dag.

**Design:**
- Ren efterbehandling — ingen ny datahentning. Ny funktion i `classify.py` (eller
  nyt `pairs.py`): for hver (model, gen) med par-mål i config: find de to billigste
  AKTIVE enkeltannoncer (quantity=1, klassifikation != UKENDT), summér
  price_per_unit_dkk (som allerede er landed cost).
- Match mod `thresholds.<key>.godt_koeb_max` (par-tærsklen, IKKE den dynamiske
  klassifikation — målet er brugerens eksplicitte prisramme).
- Geografi-hensyn: par på tværs af kontinenter giver dobbelt fragt — v1: begræns
  til samme origin-region (EU/EU eller samme land); config-flag
  `mixed_pair.same_region_only: true`.
- Notify: egen sektion "🧩 BLANDET PAR MULIGT" med begge links, samlet pris og
  afstand til mål (fx "7.154 kr — 654 kr over mål"). Vis også near-miss op til
  +15% over mål (config: `mixed_pair.near_miss_pct: 15`).
- "Aktiv" er svagt defineret (vi ved ikke om annoncer er solgt) — accepter
  false positives i v1; F5's last_seen strammer det senere.

**Berørte filer:** `classify.py`/`pairs.py` (ny), `monitor.py`, `notify.py`,
`config.yaml`, `dashboard.py` (evt. egen par-sektion).

**Acceptkriterier:**
- Kendt testdata (to 910A-singler under/over mål) giver/undlader alarm korrekt.
- Par under mål sorteres øverst i notifikationen, over GODT KØB-singler.
- Ingen alarm på tværs af regioner når same_region_only=true.

**Risici:** Solgte-men-ikke-fjernede annoncer giver falske par. Accepteret i v1.

---

## F7: Hyppigere kørsel

**Problem:** 6-timers interval er for langsomt til Kleinanzeigen, hvor kup
forsvinder på timer.

**Design:**
- launchd `StartInterval` 21600 → 7200 (2 timer) i README-plisten.
- MEN differentiér: Reverb/Thomann tåler høj frekvens; Kleinanzeigen/Blocket øger
  blokeringsrisiko. v1: én plist, 2 timer, alle kilder — men tilføj i config
  `sources_min_interval_hours: {kleinanzeigen: 4, blocket: 4}`: monitor.py gemmer
  last_run-tidsstempel pr. kilde (lille tabel `source_state` eller genbrug
  thomann_stock_state-mønstret) og springer kilden over hvis den kørte for nylig.
  Så giver én hyppig cron differentieret kadence uden flere plists.
- Dokumentér i README at recompute/dashboard ikke behøver følge kadencen.

**Berørte filer:** `README.md` (plist), `config.yaml`, `monitor.py`, `db.py`
(source_state-tabel).

**Acceptkriterier:**
- 2-timers kørsel: Reverb/Thomann kører hver gang; Kleinanzeigen/Blocket max
  hver 4. time (verificér via log over 3 kørsler).
- --source <navn> ignorerer min-interval (manuel fejlsøgning skal altid kunne køre).

**Risici:** Mere trafik mod Kleinanzeigen samlet set — overvåg bot-wall-hyppighed
i loggen efter udrulning og skru ned ved behov.
