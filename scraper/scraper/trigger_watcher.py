"""Poll Turso's `control` table for a "Kør nu" flag set from the webapp, and
run a full scraper pass on demand instead of waiting for the next hourly
launchd run. See frontend/index.html's "Kør nu" button (POST /api/trigger)
and worker/src/index.ts's /api/status, /api/trigger endpoints.

Inspired by PLAGG's trigger_watcher.py - same design: a long-running poll
loop, run as its OWN separate launchd job (see infra/launchd/watcher.template.plist
+ `make install-launchd-watcher`), alongside (not instead of) the existing
hourly scraper job. Each triggered run is a fresh `scraper.main` SUBPROCESS,
deliberately: `main.run()` calls `configure_logging()` itself, so repeated
in-process calls in this long-lived loop would stack duplicate logging
handlers on every triggered run. A subprocess call sidesteps that regardless
of what main.py's own logging/signal setup does, and matches how the hourly
launchd job already invokes the scraper (a fresh process every time).

Run: python -m scraper.trigger_watcher
"""
from __future__ import annotations

import datetime
import logging
import signal
import subprocess
import sys
import time
from pathlib import Path

from scraper_core.config import get_settings
from scraper_core.turso_client import TursoClient

logger = logging.getLogger("scraper.trigger_watcher")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUN_TIMEOUT_S = 30 * 60
POLL_INTERVAL_S = 15

CONTROL_SCHEMA = """
CREATE TABLE IF NOT EXISTS control (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    run_now INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'Klar',
    last_run_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""
CONTROL_SEED = "INSERT OR IGNORE INTO control (id) VALUES (1)"


def read_run_now(turso: TursoClient) -> bool:
    result = turso.execute("SELECT run_now FROM control WHERE id = 1")
    return bool(result.rows and result.rows[0][0])


def set_status(turso: TursoClient, text: str) -> None:
    turso.execute(
        "UPDATE control SET status = ?, updated_at = datetime('now') WHERE id = 1", (text,)
    )


def finish_run(turso: TursoClient, status_text: str, last_run_text: str) -> None:
    turso.execute(
        "UPDATE control SET run_now = 0, status = ?, last_run_at = ?, "
        "updated_at = datetime('now') WHERE id = 1",
        (status_text, last_run_text),
    )


def run_scraper_subprocess() -> tuple[bool, str]:
    """Runs `python -m scraper.main` as a separate subprocess. Returns
    (success, status_message) - never raises, so poll_once() can always
    write SOMETHING back to the control row."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "scraper.main"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        msg = f"Fejlede: koerslen tog over {RUN_TIMEOUT_S // 60} min. og blev afbrudt"
        logger.error("trigger_watcher: %s", msg)
        return False, msg

    if result.returncode != 0:
        tail_lines = [ln.strip() for ln in (result.stderr or "").splitlines() if ln.strip()]
        short_err = tail_lines[-1] if tail_lines else f"exit-kode {result.returncode}"
        msg = f"Fejlede: {short_err[:200]}"
        logger.error(
            "trigger_watcher: scraper.main fejlede (exit %d): %s", result.returncode, short_err
        )
        return False, msg

    msg = f"Færdig kl. {datetime.datetime.now().strftime('%H:%M')}"
    logger.info("trigger_watcher: scraper.main koerte igennem - %s", msg)
    return True, msg


def poll_once(turso: TursoClient) -> bool:
    """One round of the poll loop. Returns True if a run was triggered (used
    by tests to confirm the flow actually fired something)."""
    try:
        run_now = read_run_now(turso)
    except Exception:
        logger.exception(
            "trigger_watcher: kunne ikke laese run_now-flaget denne runde, proever igen naeste"
        )
        return False

    if not run_now:
        return False

    logger.info("trigger_watcher: 'Koer nu' er sat - starter koersel")
    try:
        set_status(turso, "Kører... (kan tage flere minutter)")
    except Exception:
        logger.exception("trigger_watcher: kunne ikke saette status='Kører...' (fortsaetter)")

    # ALT fra selve koerslen fanges her - watcheren maa ALDRIG crashe/stoppe med
    # at polle, uanset hvad der gaar galt i scraper.main eller i skrivningen af
    # resultatet tilbage til control-taellen.
    try:
        success, status_msg = run_scraper_subprocess()
    except Exception as e:
        logger.exception("trigger_watcher: uventet fejl under koersel-forsoeg")
        success, status_msg = False, f"Fejlede: {str(e)[:200]}"

    try:
        finish_run(turso, status_msg, datetime.datetime.now().strftime("%d-%m-%Y %H:%M"))
    except Exception:
        logger.exception(
            "trigger_watcher: koersel afsluttet (success=%s) men kunne ikke opdatere "
            "control-taellen - run_now staar muligvis stadig sat",
            success,
        )
    return True


def _install_signal_handlers() -> list:
    """SIGINT (Ctrl+C) og SIGTERM (launchctl stop / kill) skal begge stoppe
    loopet PAENT - dvs. faerdiggoere en evt. igangvaerende koersel og skrive
    resultatet, ikke bare doe midt i en subprocess."""
    stop_flags = [False]

    def handler(signum, _frame):
        logger.info("trigger_watcher: modtog signal %s - stopper paent efter denne runde", signum)
        stop_flags[0] = True

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    return stop_flags


def _sleep_or_stop(seconds: float, stop_flags: list) -> None:
    deadline = time.monotonic() + seconds
    while not stop_flags[0] and time.monotonic() < deadline:
        time.sleep(min(1.0, deadline - time.monotonic()))


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    if not settings.turso_configured:
        logger.error(
            "trigger_watcher: TURSO_DATABASE_URL/TURSO_AUTH_TOKEN not set - der er intet "
            "'Kør nu'-flag at polle uden Turso (kun relevant i local-only-tilstand). Stopper."
        )
        return 1

    with TursoClient(settings) as turso:
        turso.execute(CONTROL_SCHEMA)
        turso.execute(CONTROL_SEED)
        logger.info(
            "trigger_watcher: poller control-taellen hvert %ss (Ctrl+C for at stoppe)",
            POLL_INTERVAL_S,
        )

        stop_flags = _install_signal_handlers()
        while not stop_flags[0]:
            poll_once(turso)
            _sleep_or_stop(POLL_INTERVAL_S, stop_flags)

    logger.info("trigger_watcher: stoppet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
