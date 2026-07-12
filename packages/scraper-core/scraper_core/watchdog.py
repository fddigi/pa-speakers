"""Run a single scraper source with a wall-clock budget, so one hung/misbehaving
source never blocks an entire multi-source pipeline run indefinitely.

Built after a real production incident (PLAGG): an unexplained multi-hour hang
was never root-caused despite a 36-iteration stress test, and was worked
around with an external watchdog rather than a targeted fix. This gives every
project on this boilerplate that same backstop for free, instead of each one
needing to build its own ad-hoc timeout wrapper around a multi-source loop.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class SourceTimeoutError(Exception):
    """Raised when a source function exceeds its allotted wall-clock budget."""


def run_with_timeout(fn: Callable[[], T], *, timeout_seconds: float, source_name: str) -> T:
    """Runs fn() with a wall-clock timeout budget, returning its result or
    raising SourceTimeoutError.

    LIMITATION, by design: this cannot forcibly kill fn() if it is truly
    blocked (e.g. a socket read with no timeout of its own) - CPython cannot
    safely terminate a running thread. What it DOES guarantee: the CALLER gets
    control back after timeout_seconds regardless, so one hung source can't
    block the rest of a multi-source pipeline run forever, even if that
    source's own background thread lingers until the process eventually
    exits. Prefer giving underlying HTTP/Playwright calls their own
    request-level timeouts as the first line of defense - this is a backstop
    for what those don't catch (e.g. an infinite retry loop), not a
    replacement for them.

    Typical usage in a multi-source pipeline:

        for name, module in SOURCE_MODULES.items():
            try:
                listings = run_with_timeout(
                    lambda: module.fetch(config), timeout_seconds=300, source_name=name
                )
            except SourceTimeoutError:
                continue  # already logged; move on to the next source
            except Exception:
                logger.exception("%s: source failed, skipping", name)
                continue
            ...
    """
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError as exc:
        logger.warning(
            "watchdog: source '%s' exceeded %.0fs budget - skipping, continuing with remaining",
            source_name,
            timeout_seconds,
        )
        raise SourceTimeoutError(
            f"source '{source_name}' exceeded {timeout_seconds}s timeout"
        ) from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=False)
