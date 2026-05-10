# aitap — developer Makefile
#
# Every target is a thin wrapper over a `uv` invocation, so each command
# also works as a one-liner if you don't have `make` (Windows users can
# either install GNU Make via scoop/chocolatey, run the equivalent uv
# command directly, or use WSL).

UV ?= uv
PYTHON_VERSION ?= 3.12

.PHONY: help install lint format test test-cov typecheck docs docs-build build clean

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Sync dev + docs dependencies
	$(UV) sync --group dev --group docs

lint: ## Run ruff check + format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format: ## Auto-fix lint and format
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck: ## Run pyright in strict mode
	$(UV) run pyright

test: ## Run the test suite
	$(UV) run pytest

test-cov: ## Run tests with coverage
	$(UV) run pytest --cov=aitap --cov-report=term-missing --cov-report=html

docs: ## Serve the docs locally at http://127.0.0.1:8000
	$(UV) run --group docs mkdocs serve

docs-build: ## Build the docs into ./site
	$(UV) run --group docs mkdocs build --strict

build: ## Build sdist + wheel into ./dist
	$(UV) build

clean: ## Remove build / cache artifacts
	rm -rf build dist *.egg-info site .pytest_cache .ruff_cache .mypy_cache .pyright htmlcov coverage.xml
