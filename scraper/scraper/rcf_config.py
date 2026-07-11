"""Loads this project's RCF/Yamaha-specific config.yaml (search terms, price
thresholds, import-cost estimates, Playwright settings).

Kept separate from scraper_core.config.Settings on purpose: that shared class only
covers framework-level env vars (TURSO_*, LOCAL_SQLITE_PATH, HEALTHCHECK_URL,
LOG_LEVEL) common to every project built on this boilerplate. This project's
business-logic config (nested thresholds per model/generation, search term lists,
EU country codes) doesn't fit flat .env vars, so it stays in its own YAML file,
loaded relative to the working directory (repo root) -- same convention the
original PA SPEAKERS project's monitor.py used.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path("config.yaml")


def load_config(path: str | Path | None = None) -> dict:
    # RCF_CONFIG_PATH lets CI point at a sources-disabled fixture (see
    # config.ci-smoke.yaml) without touching the real config.yaml -- the live
    # scrapers hit real commercial marketplaces and must never run unattended
    # against shared CI runners on every push/PR.
    config_path = Path(path) if path else Path(os.environ.get("RCF_CONFIG_PATH", DEFAULT_CONFIG_PATH))
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)
