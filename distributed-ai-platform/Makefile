.PHONY: init up up-dev down reset clean build test test-unit lint lint-full format setup-hdfs frontend-build go-test

init:
	pip install -e .[dev]

up:
	docker compose up -d

# Also starts the two throwaway SSH provider containers (profile "dev").
up-dev:
	docker compose --profile dev up -d

# Stops everything and keeps your data (database, datasets, registered nodes).
down:
	docker compose --profile dev down

# Stops everything and DELETES the database and HDFS volumes.
reset:
	docker compose --profile dev down -v

# Removes caches and build output. Leaves node_modules and your data alone.
clean:
	find . -name __pycache__ -type d -prune -not -path "*/node_modules/*" -exec rm -rf {} +
	rm -rf .mypy_cache .ruff_cache .pytest_cache .coverage coverage.xml htmlcov *.egg-info
	rm -rf frontend/dist distributed-compute-vscode/dist distributed-compute-vscode/*.vsix
	find . -name .DS_Store -not -path "*/node_modules/*" -delete

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
