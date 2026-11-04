.DEFAULT_GOAL := help
.PHONY: help require-uv install install-app run cli test test-core check format clean

EXTRAS ?= gui,crypto-extra,audio,documents,jpeg,ecc,raw
CACHES := .mypy_cache .ruff_cache .pytest_cache .coverage .coverage.* htmlcov coverage.xml build dist

help: ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  make %-12s %s\n", $$1, $$2}'

require-uv:
	@command -v uv >/dev/null || { \
		echo "uv is not installed: https://docs.astral.sh/uv/getting-started/installation/" >&2; \
		exit 1; \
	}

install: require-uv ## Create .venv with every extra and the dev tools
	uv sync

install-app: require-uv ## Install ciphst and ciphst-gui for your user (EXTRAS=gui,audio to pick)
	uv tool install --force ".[$(EXTRAS)]"

run: require-uv ## Start the desktop app
	uv run ciphst-gui

cli: require-uv ## Run the command line, e.g. make cli ARGS="capacity cover.png"
	uv run ciphst $(ARGS)

test: require-uv ## Run the test suite (headless)
	QT_QPA_PLATFORM=offscreen uv run pytest

test-core: require-uv ## Run the test suite on a core-only install (no extras)
	uv run --isolated --locked --no-dev --group test pytest

check: require-uv ## Run every CI check: lint, format, types, tests
	uv run ruff check
	uv run ruff format --check
	uv run mypy
	QT_QPA_PLATFORM=offscreen uv run pytest

format: require-uv ## Format the code
	uv run ruff format

clean: ## Remove caches and build output (keeps .venv)
	rm -rf $(CACHES)
	find . \( -name .venv -o -path ./benchmarks/detectability/work \) -prune \
		-o -type d -name __pycache__ -prune -exec rm -rf {} +
