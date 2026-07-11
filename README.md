# scraper-boilerplate

Skabelon-repo (tænkt som GitHub "template repository") for mønsteret:

**Python-scraper på en Mac Mini (launchd) → lokal SQLite → delta-sync til Turso/libSQL
→ Cloudflare Worker (API-proxy) → statisk vanilla HTML/JS-frontend på GitHub Pages.**

Bygget fordi mønsteret var håndrullet tre gange med inkonsistent secrets-håndtering,
manuel provisionering og kosmetisk client-side "auth". Formålet er at et nyt projekt
i praksis kun kræver: "Use this template", ét bootstrap-workflow, ét CLI-kald for at
oprette den første bruger, og `make install-launchd` på Mac'en.

## Arkitektur

```mermaid
flowchart LR
    subgraph MacMini["Mac Mini (launchd)"]
        S["Python scraper\n(scraper/ + scraper-core)"] --> LDB[("Lokal SQLite\ndedup + outbox")]
    end
    LDB -- "kun nye/ændrede rækker\n(delta-sync)" --> T[("Turso / libSQL")]
    W["Cloudflare Worker\n(Hono API-proxy)"] <-- "libsql-client" --> T
    F["Statisk frontend\n(GitHub Pages)"] -- "fetch + cookie\ncredentials: include" --> W
    Bruger(("Bruger")) --> F
```

Nøgleprincipper:
- **Delta-writes, aldrig fuld-tabel-rewrites.** Dedup/"seen"-logik holdes lokalt i
  SQLite; kun nye/ændrede rækker sendes til Turso, i en batch pr. kørsel.
- **Én Worker pr. projekt**, ikke én pr. bruger. Auth er cookie-baseret, ikke en
  delt API-nøgle i frontend-JS.
- **Ingen håndrullet HTTP** mod Tursos `/v2/pipeline` - altid den officielle
  `libsql-client`-SDK (Python og TypeScript), altid parameterbinding.
- **Alt er variabelt via `.env`/secrets** - ingen hemmeligheder i kode, YAML eller
  `wrangler.toml`.

## Repo-struktur

```
scraper/                  Python-scraper for DETTE projekt (dummy-eksempel inkluderet)
packages/scraper-core/    Delt, separat pip-installérbar pakke (se dens egen README)
worker/                   Cloudflare Worker (Hono, TypeScript) - API-proxy + auth
frontend/                 Statisk vanilla HTML/JS (login + data-visning)
infra/                    provision.sh, add-user.sh, destroy.sh + delte lib-scripts
infra/launchd/            launchd .plist-template til Mac Mini'en
.github/workflows/        bootstrap.yml, deploy.yml, ci.yml
Makefile                  `make install-launchd` osv.
```

## Nyt projekt på 10 minutter

| # | Trin | Manuel / automatisk |
|---|------|---------------------|
| 1 | Klik "Use this template" på GitHub og navngiv det nye repo | **Manuel** (klik) |
| 2 | Sæt organisation-secrets ÉN GANG for hele din GitHub-organisation: `TURSO_PLATFORM_TOKEN`, `TURSO_ORG`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, og valgfrit `HEALTHCHECKS_API_KEY` | **Manuel** (kun første gang, arves af alle fremtidige projekter) |
| 3 | Kør workflowet "Bootstrap new project" (Actions-fanen → workflow_dispatch) | **Manuel trigger, automatisk indhold** - opretter Turso-db, deployer Worker + secrets, aktiverer GitHub Pages, opretter healthcheck (hvis `HEALTHCHECKS_API_KEY` er sat), skriver repo-secrets |
| 4 | `git clone` det nye repo lokalt / på Mac Mini'en | **Manuel** (kommando) |
| 5 | `cp .env.example .env` og udfyld `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN` / evt. `HEALTHCHECK_URL` (alle fra trin 3's output/repo-secrets, eller `turso db show <navn>`) | **Manuel** (udfyld værdier) |
| 6 | `./infra/add-user.sh` (secret-mode, default) - opretter admin-login | **Manuel kald, automatisk logik** - password vises ÉN gang |
| 7 | Ret `frontend/config.js`'s `API_BASE` til Worker-URL'en fra trin 3 | **Manuel** (én linje) |
| 8 | `make venv && make install-launchd` på Mac Mini'en | **Manuel kald, automatisk resten** - venv, launchd-plist, `launchctl load` |
| 9 | Åbn GitHub Pages-URL'en, log ind, se data | **Manuel verifikation** |
| 10 | Fremtidige pushes til `main` deployer Worker automatisk (`deploy.yml`); Pages opdaterer sig selv fra branchen | **Automatisk** |

Alt andet (schema-migration, secrets-hygiejne, CORS-lås, rate-limiting,
delta-sync-logik) er allerede bygget ind i skabelonen - der er intet at "huske"
per projekt ud over ovenstående ni klik/kommandoer.

## Brugeradministration (`infra/add-user.sh`)

To modes, styret via flag:

```bash
# v1 default: sætter ADMIN_USER/ADMIN_PW_HASH som Worker-secrets.
# Workeren tjekker login direkte mod disse to secrets - `users`-tabellen
# findes fra v1, men bruges ikke i denne mode.
./infra/add-user.sh
./infra/add-user.sh --secret-mode --username admin

# Fremtidig multi-user mode: INSERT/UPDATE i `users`-tabellen i Turso.
# Kræver at Worker'ens /login-handler ombygges til et table-lookup først
# (skemaet er klar fra dag ét, men denne omlægning er bevidst ikke automatisk).
./infra/add-user.sh --table-mode alice
```

Begge modes genererer et 20-tegns kryptografisk tilfældigt password
(`openssl rand -base64 15`), hasher det med PBKDF2-HMAC-SHA256 (samme metode og
parametre som Worker'en bruger til at verificere det), og printer passwordet ÉN
gang til terminalen. Det gemmes ingen andre steder af scriptet - skriv det ned i
en password-manager med det samme.

**Password-reset** = kør scriptet igen for samme bruger. Det overskriver hashen.
Der er bevidst ingen reset-mail og ingen 2FA - det er fravalgt for hobby-skala med
én (eller nogle få) brugere; en reel reset-mail-flow ville kræve en mailudbyder og
et sikkert engangslink-system, som er overkill her.

## Nedlæggelse af et projekt (`infra/destroy.sh`)

```bash
./infra/destroy.sh          # dry-run: viser hvad der ville blive slettet
./infra/destroy.sh --yes    # sletter for alvor: Worker, KV, Turso-db (+ alle dens
                             # tokens), GitHub Pages, og de repo-secrets provision.sh skrev
```

Irreversibelt - al data i Turso-databasen forsvinder. Kør uden `--yes` først for at
se præcis hvad der ville ske.

## Lokal udvikling og test

```bash
# Python (scraper-core + dummy-scraper)
make venv
make test          # unit-tests for delta-sync (mocket Turso-klient)
make lint          # ruff

# Kør scraperen lokalt uden nogen Turso-konto (graceful fallback til lokal-only):
.venv/bin/python -m scraper.main

# Worker (Cloudflare)
cd worker
npm install
npx tsc --noEmit   # typecheck
npx vitest run     # unit-tests for password-hash / session-cookie-logik
npx wrangler dev    # lokal dev-server (kræver ikke live Cloudflare-deploy)
```

## Secrets-hygiejne

- Al konfiguration læses fra `.env` (gitignored) via pydantic-settings.
  `.env.example` er den ene sandhedskilde for alle variabelnavne, inkl. dem der
  reelt lever som `wrangler secret` / GitHub-secrets og ikke i `.env` selv.
- `wrangler.toml` indeholder kun placeholder-værdier og kommentarer om hvilke
  `wrangler secret put`-kald der skal køres.
- `.gitleaks.toml` + `.pre-commit-config.yaml` scanner for secrets før commit;
  samme scan kører i `ci.yml`.

## Gratis tiers (ingen betalte tjenester påkrævet)

- Turso free: 100 databaser, 5 GB storage, 500M reads / 10M writes pr. måned.
- Cloudflare Workers free: 100.000 requests/dag (kontoniveau, deles på tværs af
  alle Workers på kontoen - hold det for øje hvis du kører flere projekter).
- GitHub free: ubegrænsede offentlige repos, Actions-minutter til hobby-brug, Pages.
- Healthchecks.io free (valgfrit): 20 checks - rigeligt til én pr. projekt.

## Hvad er IKKE bygget/eksekveret i denne skabelon

Vær ærlig med dig selv om følgende, før du regner med at alt bare virker:

- **`infra/provision.sh`, `infra/destroy.sh` og `bootstrap.yml`/`deploy.yml` er
  ALDRIG kørt ende-til-ende mod en rigtig Cloudflare- eller Turso-konto.** De er
  skrevet til at være fuldt funktionelle, syntaktisk validerede (`bash -n`) og
  idempotente, men første reelle afprøvning sker når "Run workflow" trykkes på
  `bootstrap.yml` med organisation-secrets sat (se PA SPEAKERS-testen i det
  overordnede backlog for status på dette).
- `infra/add-user.sh` og dele af `infra/lib/common.sh` (`wrangler_secret_put`,
  `gh_secret_set`) er derimod faktisk kørt lokalt under udviklingen af denne
  skabelon - men kun i deres "simuleret" gren (ingen `CLOUDFLARE_API_TOKEN` /
  `gh auth` til stede), som printer den kommando de ville have kørt i stedet for
  at kalde den. `--table-mode`'s SQL-logik er testet mod en lokal SQLite-fil.
- **`infra/lib/healthchecks.sh`s Management API-nøgle er verificeret med et
  reelt, read-only kald** (`GET /checks/` returnerede HTTP 200), men
  `healthchecks_create_or_get_check()`s POST-kald (kaldt fra `provision.sh`) er
  IKKE selv kørt mod en rigtig konto endnu - kun manuelt gennemgået.
- **`make install-launchd` er ikke kørt for alvor** (dvs. `launchctl load` er
  ikke kaldt) - det ville registrere et rigtigt tilbagevendende baggrundsjob på
  den maskine, skabelonen blev bygget på. Plist-genereringen (variabel-udfyldning)
  er valideret separat med `plutil -lint`.
- Selve dummy-scraperen (`scraper/scraper/sources/jsonplaceholder.py`) ER kørt
  end-to-end flere gange mod den offentlige testkilde `jsonplaceholder.typicode.com`
  og en lokal SQLite-fil, inkl. verifikation af at anden kørsel korrekt finder 0
  nye/ændrede rækker (dedup virker).
- For at gøre skabelonen brugbar som et rigtigt GitHub "template repository" skal
  du selv (efter du har pushet dette til dit eget GitHub-repo) slå
  **Settings → Template repository** til.

### Din tur: sådan aktiverer du det for første gang

1. Push dette repo til dit eget GitHub-repo (ikke gjort af denne skabelon - intet
   remote er konfigureret).
2. Slå "Template repository" til under repoets Settings.
3. Sæt de fire organisation-secrets nævnt i tjeklisten ovenfor.
4. Opret et nyt repo fra skabelonen og kør `bootstrap.yml`.

## Afvigelser fra opgavebeskrivelsen

- **Vitest kunne ikke køres direkte i dette repos egen sti** under udviklingen,
  fordi stien indeholder tegnet `#` (`.../# Claude tmux/...`), hvilket Vite
  fejlfortolker som en URL-fragment-markør. Selve testkoden er verificeret ved at
  køre den samme testsuite i en kopi af `worker/` uden på en sti uden `#` (alle 9
  tests bestod) - dette er en kvirk ved denne specifikke lokale mappe, ikke en fejl
  i koden, og vil ikke optræde i en normal GitHub Actions-checkout eller på en
  normal lokal sti.
- `Makefile` bruger bash `${var//search/replace}` i stedet for `sed 's#...#...#'`,
  og `$(shell basename "$(CURDIR)")` i stedet for `$(notdir $(CURDIR))` - begge
  fordi denne repos egen sti indeholder mellemrum og `#`, som ellers ville
  ødelægge hhv. sed's afgrænsningstegn og GNU Makes indbyggede path-funktioner
  (som splitter på whitespace). Løsningen er mere robust end den oprindelige plan
  og fungerer også på almindelige stier uden specialtegn.
