.PHONY: init up up-dev down build test test-unit lint lint-full format setup-hdfs frontend-build go-test

init:
	pip install -e .[dev]

up:
	docker compose up -d

# Also starts the two throwaway SSH provider containers (profile "dev").
up-dev:
	docker compose --profile dev up -d

down:
	docker compose down -v

build:
	docker compose build

test:
	pytest tests/unit/ -q

test-unit:
	pytest tests/unit/ -v --cov=services --cov=shared --cov-report=xml

# Gate: syntax errors and pyflakes findings (unused/undefined names), plus a
# non-strict mypy pass over the shared library. `lint-full` runs the complete
# ruff rule set from pyproject.toml, which the code base does not pass yet.
lint:
	ruff check --select E9,F services/ shared/ tests/
	mypy shared/

lint-full:
	ruff check services/ shared/ tests/

format:
	ruff format services/ shared/ tests/

setup-hdfs:
	bash scripts/setup_hdfs.sh

frontend-build:
	cd frontend && npm ci && npm run build

go-test:
	cd services/extension-gateway && go test ./...
