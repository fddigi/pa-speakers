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

---

## F8: Sortering fra mobil-kortvisningen

**Problem:** Sorterings-JS'en (`applySort()`, `sortKey`/`sortAsc`) fungerer
uafhængigt af viewport, MEN de eneste triggere er click-handlers på
`<th><button></button></th>` inde i `<thead>` (index.html linje 686-694). På
mobil (`@media (max-width: 640px)`) skjules HELE `<thead>` med den bevidste
sr-only-teknik (`position:absolute; clip:rect(0,0,0,0); width:1px`, linje
321-331) for at give plads til kort-layoutet og undgå den vandrette scroll der
tidligere blev fjernet. Resultatet: der findes INGEN måde at udløse en sortering
fra mobil overhovedet -- hverken efter pris eller andet. Bekræftet ved læsning,
ikke gættet: knapperne er i det klippede `<thead>`, og der er ingen anden
sort-trigger i mobil-DOM'en. Standard-sorteringen (klassifikation, så nyeste)
er fornuftig, men pris -- det mest efterspurgte -- kan ikke nås på telefonen.

**Design:**
- Tilføj ét mobil-synligt sorteringskontrol i `.controls`-rækken (ved siden af
  fritekstfilteret), fx en native `<select>` "Sortér efter": Standard,
  Pris/enhed (lav→høj), Pris/enhed (høj→lav), Først set (nyeste), Model. Native
  `<select>` giver gratis en god touch-picker og introducerer ingen vandret
  scroll (fuld bredde, wrapper i det eksisterende flex-layout).
- Skjul kontrollen på desktop (`@media (min-width: 641px) { #mobile-sort {
  display:none } }`), hvor `<thead>`-knapperne allerede virker -- undgå to
  konkurrerende sort-UI'er på samme viewport.
- GENBRUG den eksisterende motor: kontrollen sætter blot `sortKey`/`sortAsc`
  og kalder `applySort()` + `renderTable()`. Ingen ny sorteringslogik. Én
  option-værdi = "" nulstiller til standard-sorteringen (`sortKey = null`).
- Hold `<select>` og `<thead>`-pilene i synk: `updateSortIndicators()` bør også
  opdatere `<select>.value`, så en desktop-klik-sortering afspejles hvis vinduet
  gøres smalt (billig tilføjelse i samme funktion).
- Pris/enhed er allerede landed cost pr. enhed (`price_per_unit_dkk`), så
  sorteringen er retfærdig på tværs af kilder -- ingen backend-ændring.

**Berørte filer:** `frontend/index.html` (KUN denne -- CSS-media-query, ét
`<select>` i markup, én change-handler + lille tilføjelse i
`updateSortIndicators()`). Ingen scraper-, worker- eller DB-ændring.

**Acceptkriterier:**
- På ≤640px viewport kan brugeren sortere efter pris/enhed stigende OG faldende
  fra kortvisningen uden vandret scroll.
- "Standard"-valget giver præcis den nuværende kombinerede sortering
  (klassifikation, så nyeste) -- `sortKey = null`.
- Desktop uændret: `<select>` er skjult, `<thead>`-knapperne virker som før.
- Klientside-filtrene (klassifikations-chips + fritekst) bevarer den valgte
  sortering (samme `applySort()`-flow som i dag).

**Risici:** Meget lav -- rent additiv frontend-ændring i én fil. Eneste
faldgrube er dobbelt-UI hvis media-query-grænsen (640/641px) ikke matcher
kort-layoutets grænse; brug samme breakpoint. Sørg for at `<select>` ikke selv
er bredere end viewport (den er blok/fuld-bredde, så ingen ny scroll).

---

## F9: Kategorisering af søgninger (flere produktgrupper)

**Problem:** `search_terms` er i dag en HELT flad liste (config.yaml
`primary`/`secondary`, eller Turso-tabellen `search_terms(term, enabled,
created_at)` -- ingen kategori-kolonne). I main.py flades listen yderligere ud
til `{"primary": [...], "secondary": []}` (linje 100), og ALLE aktiverede
kilder søger ALLE termer -- ingen per-kategori-routing. Brugeren vil gruppere
søgninger i flere kategorier (PA-toppe + PA-sub + studie-sub + synths) for
overblik. Eksemplet antyder en UDVIDELSE af produktscopet langt ud over de 5
nuværende PA-modeller.

**Ærlig vurdering af den reelle implikation (læst i koden, ikke antaget):**
Der er to vidt forskellige fortolkninger, og de skal holdes adskilt:

- **(a) Ren organisering** af eksisterende søgetermer + tagging af annoncer med
  hvilken kategori der fandt dem. Middel-stort, cross-cutting job (rører
  scraper + worker + frontend + to skemaer), men lav semantisk risiko.
- **(b) Reelt scope til synths/studie-udstyr.** Dette er en MEGET større epic
  og bryder tre hårdkodede antagelser:
  1. `normalize.py MODEL_PATTERNS` er hårdkodet til RCF ART (910/710/708/
     sub705/712) + Yamaha DXR. Synths/studie-udstyr har helt andre
     modelnavne-mønstre (Moog Subsequent, Prophet, Genelec 8040, ...) -- det
     er ikke 5 nye regex'er, men en åben taksonomi pr. brand. Uden dem giver
     `extract_model()` `None` → annoncen bliver `UKENDT` og leverer nul
     klassifikationsværdi.
  2. `classify.py`: percentil-klassifikationen grupperer pr. **(model, gen)**
     (`WHERE model=? AND gen=?`, 180 dages lookback, min. 5 samples), IKKE pr.
     helt datasæt. Det betyder faktisk at en synth-kategori IKKE korrumperer
     RCF-percentilerne -- synths ender som `model=None`/`UKENDT` og udelukkes
     automatisk fra hver RCF-pulje (query'en filtrerer `model=?`). Men "gen"
     (MK1-5) er et RCF/Yamaha-koncept; synths ville alle samles i
     `(synth_model, "uoplyst")` -- percentil-motoren KAN genbruges pr. ny
     model, men de statiske tærskler (`thresholds`) er hårdkodede RCF-nøgler.
  3. Kilde-routing: `sources/thomann.py` poller en RCF-specifik B-Stock-
     kategoriside (`thomann_category_url`) -- den finder ALDRIG synths uanset
     søgeterm. Term-baserede kilder (Kleinanzeigen/Blocket/DBA/Reverb) ville
     søge synth-termer fint, men Thomann-kilden hører reelt kun til
     PA-kategorien. Der er altså ingen kategori→kilde-mapping i dag.

  → Design til (a) NU, og lad (b) være en eksplicit, separat epic. (a) leverer
  overblikket brugeren beder om uden at love en klassifikationsværdi systemet
  ikke kan give for nye kategorier endnu.

**Design (v1 = fortolkning (a), organisering + tagging):**
- Skema: tilføj `category TEXT NOT NULL DEFAULT 'PA-højttalere'` til
  `search_terms`-tabellen (Turso) OG et tilsvarende felt i config.yaml-
  fallback'en (`search_terms:` bliver en liste af `{term, category}` eller en
  dict pr. kategori). `search_terms.py:load_search_terms()` returnerer nu
  (term, category)-par, ikke en flad strengliste.
- main.py: STOP med at flade listen til `primary/secondary` (linje 100) på en
  måde der taber kategorien -- kilderne skal kunne se hvilken kategori en term
  hører til, så den fundne annonce kan tagges. Minimalt: behold en term→kategori
  opslagstabel og sæt `category` på hver normaliseret annonce via
  `extra["search_term"]` (Reverb sætter den allerede; de andre kilder skal
  gøre det samme).
- `listings`-tabellen: ny `category`-kolonne, sat ved indlæsning ud fra den
  term der fandt annoncen. `recompute.py`: backfill af `category` for
  eksisterende rækker (default 'PA-højttalere').
- Webapp Worker (`worker/src/index.ts`): `/api/search-terms` GET/POST/DELETE
  udvides med `category`; `/api/listings` får valgfrit `?category=`-filter.
- Frontend (`index.html`): grupper ønskeseddel-chippene pr. kategori,
  tilføj-formen får en kategori-vælger, og listen får et kategori-filter
  (samme klientside-mønster som klassifikations-chippene).
- classify.py: uændret i v1 -- den er allerede model+gen-partitioneret, så nye
  kategorier uden model-regex bliver blot `UKENDT` (ærligt, indtil (b)).

**Eksplicit UDE af v1-scope (dokumentér som opfølgnings-epic, ikke byg nu):**
nye `MODEL_PATTERNS` pr. kategori, per-kategori statiske tærskler / ikke-gen-
baseret percentil-gruppering, kategori→kilde-routing (hvilke kilder søger hvilke
kategorier), og per-kategori Thomann/kategorisides-URL'er. UDEN disse vil en
"synths"-kategori vise synth-annoncer, men klassificere dem ALLE som UKENDT og
slet ikke dække Thomann. Sæt forventningen tydeligt.

**Berørte filer:** `scraper/scraper/search_terms.py`, `scraper/scraper/main.py`,
`config.yaml`, `scraper/scraper/pipeline.py` (tag med kategori), `db.py`/skema
(`category`-kolonne på `listings` + `search_terms`), `recompute.py` (backfill),
`worker/src/index.ts` (+`worker/src/db.ts`), `frontend/index.html`. (Bemærk:
to datastore-skemamigreringer + tre lag -- scraper, worker, frontend.)

**Acceptkriterier:**
- Søgetermer kan oprettes/vises grupperet under en kategori fra webappen; en
  ny term får en kategori.
- En annonce tagges med kategorien for den term der fandt den; `?category=`
  filtrerer listen; frontend kan filtrere pr. kategori.
- Eksisterende RCF/DXR-annoncer og deres percentil-klassifikation er UÆNDRET
  (kategori-laget rører ikke model+gen-grupperingen).
- recompute.py backfiller `category` uden at ændre klassifikation.
- --dry-run skriver intet (også ingen kategori-writes).

**Risici:** STØRRE end det lyder. (1) Skemamigrering i BÅDE Turso og lokal
SQLite + worker + frontend -- cross-cutting, ikke en enkelt fil. (2) Reel
scope-fælde: hvis brugeren faktisk mener synths/studie-udstyr (fortolkning b),
kræver det ny model-taksonomi, nye klassifikationsgrupper og kilde-routing --
en separat epic på størrelse med F4+. v1 leverer BEVIDST kun organisering +
tagging og efterlader nye kategorier som UKENDT; det skal kommunikeres, ellers
opleves featuren som "halvt virkende". (3) main.py's flad-gøring af søgetermer
(linje 100) er et bevidst kompatibilitetshack for at holde alle kilders
`primary + secondary`-read i live -- at bære kategorien igennem uden at bryde
den kontrakt kræver omhu.

---

## F10: Spike: prishistorik / klikbar klassifikations-drilldown

**Problem:** Klassifikations-badget (GODT KØB / FAIR / OVERPRISET / UKENDT) er
i dag en sort boks — brugeren ser konklusionen, ikke grundlaget. Ønsket
formuleres som "prishistorik" (à la Pricerunner), men vores case er
fundamentalt anderledes end en prissammenligningsside, og det skal en spike
afklare FØR der bygges en graf på en forkert præmis.

**Kernefund fra forudgående research (dokumentér i spiken):**
- Pricerunner/lignende viser pris-over-tid for et FAST retail-SKU: samme
  vare kan genudbydes/genopfyldes i det uendelige, så der findes en stabil
  produkt-identitet at følge på tidsaksen ("kostede den mindre for 3 mdr.
  siden?").
- Reverb Price Guide (og GearBook) viser IKKE én annonces pris over tid, men
  aggregerer REALISEREDE salgspriser PR. MODEL (Reverb: ~240k produkter,
  gennemsnit + transaktionshistorik år tilbage + et "Price Index" for
  kategori/brand-trends). Dvs. tidsserien findes kun på MODEL-niveau, som et
  gennemsnit af mange salg — ikke pr. fysisk enhed.
- Vores case: hver annonce er en UNIK, brugt engangsvare der sælges én gang
  og forsvinder permanent. Der findes intet SKU at spore over tid for samme
  fysiske højttaler, og vi observerer ALDRIG realiserede salgspriser (kun
  udbudspriser ved first_seen). Vi kan derfor hverken lave Pricerunners
  SKU-tidsserie eller Reverbs sold-price-tidsserie.
- Det ENESTE der har en meningsfuld tidsdimension hos os er FORDELINGEN af
  udbudspriser på tværs af mange annoncer for samme model+gen — hvilket er
  præcis det `classify_dynamic()` allerede beregner (rullende p25/p75 over
  `lookback_days=180`, `min_samples=5`). Drilldownen skal derfor vise
  "fordelingen der lå til grund for klassifikationen", ikke "denne vares pris
  over tid".

**Design (hvad spiken skal afklare + skitse til drilldown):**
- Reframe featuren fra "prishistorik" til "klassifikations-drilldown": gør
  badget klikbart → panel der viser populationen bag percentil-beregningen.
- KONKRET indhold (verificér mod `listings`-skema — alle felter findes
  allerede: `price_per_unit_dkk`, `model`, `gen`, `classification`,
  `classification_method`, `source`, `first_seen`, `url`, `title`):
  1. Histogram/scatter over `price_per_unit_dkk` for ALLE annoncer med samme
     model+gen inden for `lookback_days` (samme datasæt classify_dynamic
     forespørger).
  2. Markerede p25- og p75-linjer (grænserne der afgør badget).
  3. Denne annonces egen pris markeret på aksen → visuel forklaring på
     hvorfor badget blev som det blev.
  4. `n` (antal datapunkter) + metode ("dynamisk (n=12)" vs "statisk
     (utilstrækkelig historik)"). Ved statisk fallback: vis at n <
     `min_samples` og hvilke faste config-tærskler der blev brugt i stedet.
  5. Liste over de sammenlignelige annoncer der udgør populationen (titel,
     kilde, pris, first_seen, link).
- Ærlig caveat der SKAL med i UI'et: det er UDBUDSPRISER for annoncer set
  inden for 180 dage, ikke realiserede salgspriser; annoncer der stadig ligger
  kan være solde/døde. Det er et øjebliksbillede af markedspopulationen, ikke
  en tidsserie for én fysisk enhed.
- Spiken skal desuden vurdere OM en graf overhovedet er umagen værd, eller om
  en letvægts-tekst-tooltip ("n=12 · p25=3.400 · p75=4.900 · denne: 3.100 kr")
  allerede leverer 80% af værdien til ~10% af arbejdet.
- Arkitektur-spørgsmål spiken skal besvare: percentil-logikken bor i Python-
  scraperen (`classify.py`), ikke i worker'en. Skal worker'en (a) replikere
  percentil-forespørgslen i et nyt endpoint (fx `GET
  /api/listings/:item_key/context`), eller (b) skal scraperen precompute og
  gemme populations-konteksten pr. annonce? Afvej duplikeret logik vs. ekstra
  lagring.

**Berørte filer (kun hvis spiken giver grønt lys — spiken selv skriver kun
en konklusion):** `worker/src/index.ts` (nyt context-endpoint), frontend/
dashboard (klikbart badge + drilldown-panel), evt. `scraper/scraper/
classify.py` (udstil populations-forespørgslen til genbrug). BEMÆRK:
`price_history`-tabellen fra F5 er IKKE bygget og er IKKE nødvendig for
fordelings-drilldownen — men den ville være en forudsætning, hvis vi senere
vil vise en ægte pris-faldt-tidslinje pr. annonce (og selv da mangler vi
realiserede salgspriser).

**Acceptkriterier (spiken er færdig når vi kan svare på):**
- Er der OVERHOVEDET en meningsfuld "pris over tid" vi kan vise, eller er det
  ærlige svar udelukkende "fordeling bag klassifikationen"? (Forventet svar
  ud fra research: kun fordeling — en Reverb-agtig sold-price-tidsserie
  kræver realiserede salgsdata vi ikke indsamler.)
- Hvad kan drilldownen konkret rendere GIVET kun `listings`-tabellen, og
  hvilke felter mangler vi? (afklaret ovenfor: histogram + p25/p75 + denne-
  markør + comparables; vi mangler sold-price og per-annonce-historik.)
- Hvor skal beregningen leve (worker-endpoint der replikerer percentiler vs.
  precomputed kontekst fra scraperen), og hvad er den mindste levedygtige
  udgave (graf vs. tekst-tooltip)?
- Konklusion: byg fuld graf / byg tekst-tooltip / drop featuren — med
  begrundelse.

**Risici:** Lav teknisk risiko (kun læse-visning af data vi har). Største
risiko er at bygge en "prishistorik"-graf der IMPLICIT lover en tidsserie vi
ikke har data til, og dermed vildleder brugeren til at tro et tal er en
realiseret markedspris. Spiken mitigerer netop dette ved at fastlægge den
ærlige framing før kode.

---

## F11: Spike: flere kilder (Gearloop, Thomann nypris-reference, Facebook)

**Problem:** 5 nuværende kilder dækker EU/Norden-brugtmarkedet delvist. Tre
kandidater er nævnt: Gearloop, en Thomann NYPRIS-reference (ikke B-Stock), og
brugte Facebook-sider/grupper. De har VIDT forskellige risikoprofiler og skal
vurderes hver for sig før byg — især Facebook, der ToS-mæssigt og teknisk er i
en helt anden liga end de nuværende kilder.

**Kernefund fra forudgående research (dokumentér i spiken):**

- **Gearloop = gearloop.se** — "Sveriges marknadsplats för musikutrustning",
  en svensk køb/salg-markedsplads for brugt musikudstyr med egne kategorier
  for "PA & Live" og "Studio & Scenutrustning". Direkte relevant for
  RCF ART/Yamaha DXR-segmentet og samme SE-marked som Blocket (afhentning i
  SE, evt. fragt). Fremstår server-renderet HTML (kategori- og annonce-URL'er
  er crawl-bare, SEK-priser). Ingen offentligt dokumenteret API fundet —
  scrapeability skal verificeres empirisk, men risikoprofilen ligner de
  eksisterende HTML-kilder (lav-medium). MEST LOVENDE kandidat.

- **Thomann NYPRIS-reference** — Thomanns almindelige produktside (fx
  `thomann.de/de/rcf_art_910_a.htm`) viser nyprisen i STATISK HTML (verificeret
  2026-07-12: 579 € / UVP 679 € for ART 910-A, "Sofort lieferbar"). Modsat
  B-Stock-URL'erne (som `thomann.py` allerede håndterer, og som 404'er når der
  ikke er B-Stock på lager) er den almindelige produkt-URL STABIL og altid
  til stede. Prisen kan parses med præcis samme teknik som `_fetch_price_dkk`
  i den nuværende `thomann.py`. Lav teknisk risiko, men også lavere værdi:
  nyprisen er et velkendt loft, ikke et kup.

- **Facebook Marketplace/grupper** — MARKANT anden risikoprofil. Metas ToS
  ("Automated Data Collection Terms") forbyder eksplicit scrapers/bots/
  crawlers. Facebook kører aggressiv anti-automation: CAPTCHA, rate limiting,
  JS-obfuskering, browser-fingerprinting, TLS/HTTP2-tjek og ML-baseret
  bot-detektion. En californisk dom (Meta v. Bright Data, 2024) fandt at
  scraping af OFFENTLIGE/udloggede data ikke i sig selv brød ToS — MEN
  Marketplace/grupper kræver reelt login for at browse meningsfuldt, og dommen
  skelnede netop mellem at omgå anti-bot på offentlige data (ok) og at
  gennembryde en login-mur (ikke ok). Vores case falder på den forkerte side
  af den skelnen. Dette er en juridisk OG teknisk risiko der ikke findes hos
  de 5 nuværende kilder (som alle kan hentes udlogget).

**Design (hvad spiken skal afklare pr. kilde):**
- **Gearloop (undersøg FØRST):** Har siden et JSON-API eller `__NEXT_DATA__`/
  serialiseret state? Er kategori-/søgeresultatet server-renderet med stabile
  selectors? SEK-priser der kan genbruge eksisterende `to_dkk`? Volumen af
  RCF/Yamaha-udbud lige nu? Paginering? `robots.txt`/ToS? Kan den passe ind i
  den eksisterende kilde-kontrakt (`fetch(config, dry_run) -> list[raw]`,
  `origin_country_code="SE"`)?
- **Thomann nypris:** Bekræft at parsingen af `.price` virker på den
  almindelige produktside (ikke kun B-Stock). Afklar ROLLEN — anbefaling:
  brug den som et DISPLAY-ANKER / loft ("nypris: X kr") ved siden af
  brugtannoncer, IKKE som input til percentil-historikken (nypriser ville
  forurene den brugte fordeling og skæve klassifikationen). Kræver en lille
  config-mapping model+gen → Thomann produkt-URL (håndtér varianter: 910A vs
  910AX osv.). Skal den have sin egen tabel/visning adskilt fra `listings`
  (à la `thomann_stock_state`)?
- **Facebook:** Afklar ærligt om det overhovedet er teknisk/juridisk
  forsvarligt for et personligt værktøj. Forventet konklusion (skal
  bekræftes, ikke antages): MARKÉR SOM BEVIDST UDELUKKELSE med begrundelse —
  ToS-forbud + login-mur + aggressiv anti-bot gør risiko/værdi-forholdet
  uacceptabelt sammenlignet med de øvrige kilder. Brugeren kan i stedet bruge
  Facebooks egne "gemte søgninger"/notifikationer manuelt.

**Anbefalet rækkefølge:** (1) Gearloop — højest værdi, lavest risiko, udvider
det SE-segment vi allerede kender fra Blocket. (2) Thomann nypris — triviel og
lav risiko, men lav værdi (kun et referenceloft). (3) Facebook — undersøg kun
nok til at DOKUMENTERE udelukkelsen; forfølg ikke.

**Berørte filer (kun ved grønt lys — spiken selv skriver en konklusion pr.
kilde):** `scraper/scraper/sources/gearloop.py` (ny), `sources/thomann.py`
(eller ny `thomann_new.py` til nypris-reference), `config.yaml`,
`monitor.py`/`main.py` (SOURCE_MODULES), `README.md`.

**Acceptkriterier (spiken er færdig når vi kan svare på):**
- Gearloop: statisk HTML eller SPA/API? Stabile selectors? SEK-priser?
  RCF/Yamaha-volumen? ToS/robots ok? → byg-ja/nej med begrundelse.
- Thomann nypris: virker parsing på den almindelige produktside? Klassifikations-
  input eller kun display-anker? (anbefaling: kun anker) → skitse til
  integration + model→URL-mapping.
- Facebook: er ToS/teknisk risiko afklaret og dokumenteret? → klar ja/nej
  (forventet: nej, bevidst udelukkelse).
- Samlet: prioriteret rækkefølge for eventuel efterfølgende byg.

**Risici:** Gearloop kan vise sig at være JS-renderet/API-beskyttet (afklares
billigt i spiken). Thomann nypris kan friste til at blande ny- og brugtpriser
i klassifikationen — hold dem adskilt. Facebook: reel konto-/juridisk risiko
ved at forsøge — spiken skal netop STOPPE et forhastet byg her, ikke muliggøre
det.

**Spike-konklusion (afklaret 2026-07-13, kode bygget hvor der var grønt lys):**

- **Gearloop → BYG-JA, bygget:** Viste sig at være en Next.js App Router-SPA
  (ikke server-renderet HTML som antaget) — søgeresultater er IKKE i den
  statiske HTML, kun i client-side hydration. Playwright var derfor
  nødvendigt, ikke valgfrit; `requests`-baseret hentning ville have givet
  0 resultater uden fejl, hver gang. Selectors bekræftet mod reelt markup
  (`article`-kort, `h3 a`-titel, relativ href i en usynlig
  `a[aria-hidden='true']`, pris via regex mod hele kortets tekst — ingen
  stabil pris-specifik CSS-klasse fundet). `robots.txt` tillader generel
  crawling. Implementeret i `scraper/scraper/sources/gearloop.py`, kørt
  reelt via `--source gearloop` — lavt volumen af RCF/Yamaha-udbud på
  hentningstidspunktet (0 relevante annoncer, ét fravalgt "köpes"-opslag for
  en anden model), men kilden virker og er klar til at fange fremtidigt
  udbud.
- **Thomann nypris → BYG-JA (skitse), delvist bygget:** Parsing af
  `.price-and-availability .price-wrapper .price` bekræftet at virke
  uændret på den almindelige (ikke B-Stock) produktside — testet reelt mod
  `thomann.de/de/rcf_art_910_a.htm` (579 € → 4.319,34 kr med
  `currency.eur_dkk`). Implementeret som rent display-anker i
  `scraper/scraper/thomann_new_price.py` (egen tabel
  `thomann_new_price_ref`, upsert pr. model — IKKE input til
  percentil-klassifikationen), med Worker-endpoint
  `GET /api/thomann-new-price` og en linje i frontend'en. Kun ÉN model er
  kortlagt (910A) — forsøg på at bekræfte URL-slugs for de øvrige 7 modeller
  (710A/708A/SUB705/712A + Yamaha DXR8/10/12/15) i en hurtig burst udløste
  Thomanns rate-limiter (429) på de fleste af dem, plus to reelle 404
  (forkert gættet slug-mønster) og én uafklaret 301-redirect. Konklusion:
  mønsteret virker, men resten af model→URL-mapningen skal bekræftes
  ENKELTVIS med god tid imellem forespørgslerne — ikke en burst — for ikke
  at blive rate-limitet igen. `MODEL_NEW_PRICE_URLS`-dictet i
  `thomann_new_price.py` er allerede struktureret til at tage flere modeller
  uden kodeændringer, når/hvis flere slugs bekræftes.
- **Facebook → BEVIDST UDELUKKET, som forventet:** Ingen ny research ud over
  det allerede dokumenterede — ToS-forbud + login-mur + aggressiv anti-bot
  gør risiko/værdi-forholdet uacceptabelt for et personligt værktøj. Ingen
  kode bygget. Brugeren kan i stedet bruge Facebooks egne "gemte
  søgninger"/notifikationer manuelt.
- **Samlet prioritet for evt. videre arbejde:** (1) Thomann nypris — bekræft
  resten af model→URL-mapningen enkeltvis, lav risiko/indsats. (2) Gearloop
  — ingen videre kodearbejde nødvendigt, men genbesøg volumen om nogle
  måneder. (3) Facebook — forfølg ikke.
