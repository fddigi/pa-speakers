"""F9: lille hjælper til additive, idempotente skemamigreringer (ALTER TABLE
ADD COLUMN) på tabeller der allerede eksisterede FØR en ny kolonne blev
tilføjet. `CREATE TABLE IF NOT EXISTS` alene tilføjer IKKE en kolonne
retroaktivt til en allerede-eksisterende tabel -- hverken på den lokale
SQLite-fil eller i Turso, som begge har rigtige, allerede-udfyldte
`listings`/`search_terms`-tabeller fra før F9.
"""
from __future__ import annotations


def add_column_if_missing(conn, table: str, column: str, column_ddl: str) -> None:
    """`conn` er hvad som helst med en `.execute(sql)` -- en sqlite3.Connection
    eller en TursoClient. Sikker at kalde ved HVER køring: tjekker via
    `PRAGMA table_info` FØR den forsøger ALTER, i stedet for at forsøge ALTER
    og fange en "duplicate column"-fejl bagefter.

    Fundet 2026-07-13: den fange-fejlen-bagefter-tilgang virker IKKE pålideligt
    mod Turso via `libsql_client` -- HTTP-transportens fejlhåndtering
    (`libsql_client/http.py`) kaster en uigennemsigtig `KeyError: 'result'` i
    stedet for en exception hvis besked indeholder "duplicate column", så
    `str(exc)`-tjekket aldrig kunne genkende den og re-raisede fejlagtigt.
    PRAGMA table_info fungerer derimod pålideligt mod Turso (almindeligt
    SELECT-lignende resultat, ikke en fejlsti) - se check-først i stedet."""
    result = conn.execute(f"PRAGMA table_info({table})")
    # TursoClient.execute() returns a ResultSet (`.rows`); sqlite3.Connection.execute()
    # returns a Cursor (`.fetchall()`) - this helper supports both callers.
    rows = result.rows if hasattr(result, "rows") else result.fetchall()
    existing_columns = {row[1] for row in rows}
    if column in existing_columns:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_ddl}")
