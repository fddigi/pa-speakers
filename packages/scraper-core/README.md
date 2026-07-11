# scraper-core

Delt Python-bibliotek for mønsteret: hent data -> dedup lokalt i SQLite -> deltasync til Turso.

> **Note om placering:** I en "rigtig" multi-projekt-opsætning bør denne pakke ligge i sit
> eget separate git-repo (fx `github.com/<dig>/scraper-core`) og installeres i hvert
> scraper-projekt via `pip install git+https://github.com/<dig>/scraper-core.git@vX.Y.Z`.
> Her i boilerplaten ligger den som en undermappe i samme repo for at holde skabelonen
> "use this template"-simpel. Når du har brugt skabelonen til dit andet eller tredje
> projekt, er det værd at rykke `packages/scraper-core` ud i sit eget repo og pinne en
> version, så du ikke risikerer at tre projekter driver fra hinanden igen.

## Indhold

- `scraper_core.config` — pydantic-settings-baseret konfiguration, læser `.env`.
- `scraper_core.local_db` — lokal SQLite-håndtering: skema, "seen"-dedup, delta-udtræk.
- `scraper_core.turso_client` — tynd wrapper omkring den officielle `libsql-client`
  Python-SDK. Ingen håndrullet HTTP, altid parameterbinding.
- `scraper_core.sync` — selve delta-sync-logikken (kun nye/ændrede rækker sendes).
- `scraper_core.healthcheck` — no-op-safe ping til healthchecks.io.
- `scraper_core.logging_setup` — struktureret logging via `rich`.

## Installation (lokalt, i editable mode)

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e packages/scraper-core
```

## Test

```bash
pip install -e "packages/scraper-core[dev]"
pytest packages/scraper-core/tests
```

Testene mocker Turso-klienten, så de kan køre uden en rigtig Turso-konto eller netværk.
