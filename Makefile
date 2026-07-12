# Local dev + launchd install for the Mac Mini side of this project.
#
# Override any of these on the command line, e.g.:
#   make install-launchd SCRAPE_INTERVAL_SECONDS=1800

# Needed for the ${var//search/replace} substitution below (not available in
# plain /bin/sh, and macOS's stock /bin/bash 3.2 does support it).
SHELL := /bin/bash

# $(shell basename ...), not $(notdir $(CURDIR)): GNU Make's notdir/dir/basename
# functions split their argument on whitespace and process each word separately,
# which silently mangles paths containing spaces (as this repo's own checkout
# path does). Shelling out to the real `basename` avoids that.
PROJECT_NAME            ?= $(shell basename "$(CURDIR)")
PYTHON_VENV             ?= $(CURDIR)/.venv
PYTHON_BIN              ?= $(PYTHON_VENV)/bin/python
SCRAPE_INTERVAL_SECONDS ?= 3600
LAUNCHD_LABEL           ?= com.$(PROJECT_NAME).scraper

PLIST_TEMPLATE := infra/launchd/scraper.template.plist
PLIST_DEST     := $(HOME)/Library/LaunchAgents/$(LAUNCHD_LABEL).plist
LOG_DIR        := $(CURDIR)/data/logs

WATCHER_LABEL         ?= com.$(PROJECT_NAME).trigger-watcher
WATCHER_PLIST_TEMPLATE := infra/launchd/watcher.template.plist
WATCHER_PLIST_DEST     := $(HOME)/Library/LaunchAgents/$(WATCHER_LABEL).plist

.PHONY: venv test lint install-launchd uninstall-launchd install-launchd-watcher uninstall-launchd-watcher

venv:
	python3.11 -m venv $(PYTHON_VENV)
	$(PYTHON_BIN) -m pip install --upgrade pip
	$(PYTHON_BIN) -m pip install -e "packages/scraper-core[dev]"
	$(PYTHON_BIN) -m pip install -e "scraper[dev]"

test:
	$(PYTHON_BIN) -m pytest packages/scraper-core/tests -q

lint:
	$(PYTHON_BIN) -m ruff check packages/scraper-core scraper

# Generates a launchd .plist from infra/launchd/scraper.template.plist (filling
# in the venv's python path, this repo's absolute path, the run interval and a
# log directory), installs it into ~/Library/LaunchAgents/, and loads it.
#
# Uses bash's ${var//search/replace} rather than `sed -e 's#...#...#'` on
# purpose: sed needs a delimiter character that can't appear in the substituted
# values, but an absolute path can contain almost anything (this repo's own
# checkout path contains "#", which breaks a "#"-delimited sed one-liner).
install-launchd:
	@if [ ! -x "$(PYTHON_BIN)" ]; then \
		echo "error: $(PYTHON_BIN) not found - run 'make venv' first."; \
		exit 1; \
	fi
	@mkdir -p "$(LOG_DIR)"
	@mkdir -p "$(HOME)/Library/LaunchAgents"
	@content="$$(cat "$(PLIST_TEMPLATE)")"; \
	content="$${content//__LABEL__/$(LAUNCHD_LABEL)}"; \
	content="$${content//__PYTHON_BIN__/$(PYTHON_BIN)}"; \
	content="$${content//__WORKING_DIR__/$(CURDIR)}"; \
	content="$${content//__INTERVAL_SECONDS__/$(SCRAPE_INTERVAL_SECONDS)}"; \
	content="$${content//__LOG_DIR__/$(LOG_DIR)}"; \
	printf '%s\n' "$$content" > "$(PLIST_DEST)"
	@echo "Wrote $(PLIST_DEST)"
	-launchctl unload "$(PLIST_DEST)" 2>/dev/null
	launchctl load "$(PLIST_DEST)"
	@echo "Loaded launchd job '$(LAUNCHD_LABEL)' - runs every $(SCRAPE_INTERVAL_SECONDS)s (and once now)."
	@echo "Logs: $(LOG_DIR)/scraper.out.log / scraper.err.log"

uninstall-launchd:
	-launchctl unload "$(PLIST_DEST)" 2>/dev/null
	rm -f "$(PLIST_DEST)"
	@echo "Removed $(PLIST_DEST)"

# Installs the SEPARATE, always-running "Kør nu" watcher (scraper/scraper/
# trigger_watcher.py) - polls Turso's control row for the webapp's run-now
# trigger and starts a scraper run on demand. Runs ALONGSIDE the hourly
# install-launchd job, not instead of it (main.py's own advisory file lock
# stops the two from ever racing on the same local SQLite file if they
# happen to overlap). Requires Turso to be configured (see .env) - there is
# no "Kør nu" flag to poll without it.
install-launchd-watcher:
	@if [ ! -x "$(PYTHON_BIN)" ]; then \
		echo "error: $(PYTHON_BIN) not found - run 'make venv' first."; \
		exit 1; \
	fi
	@mkdir -p "$(LOG_DIR)"
	@mkdir -p "$(HOME)/Library/LaunchAgents"
	@content="$$(cat "$(WATCHER_PLIST_TEMPLATE)")"; \
	content="$${content//__LABEL__/$(WATCHER_LABEL)}"; \
	content="$${content//__PYTHON_BIN__/$(PYTHON_BIN)}"; \
	content="$${content//__WORKING_DIR__/$(CURDIR)}"; \
	content="$${content//__LOG_DIR__/$(LOG_DIR)}"; \
	printf '%s\n' "$$content" > "$(WATCHER_PLIST_DEST)"
	@echo "Wrote $(WATCHER_PLIST_DEST)"
	-launchctl unload "$(WATCHER_PLIST_DEST)" 2>/dev/null
	launchctl load "$(WATCHER_PLIST_DEST)"
	@echo "Loaded launchd job '$(WATCHER_LABEL)' - polls for the webapp's 'Kør nu' button continuously."
	@echo "Logs: $(LOG_DIR)/trigger-watcher.out.log / trigger-watcher.err.log"

uninstall-launchd-watcher:
	-launchctl unload "$(WATCHER_PLIST_DEST)" 2>/dev/null
	rm -f "$(WATCHER_PLIST_DEST)"
	@echo "Removed $(WATCHER_PLIST_DEST)"
