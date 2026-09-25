.PHONY: up down build test lint format init

init:
	pip install -e .[dev]

up:
	docker compose up -d

down:
	docker compose down -v

build:
	docker compose build

test-unit:
	pytest tests/unit/ -v --cov=services --cov=shared

test-integration:
	pytest tests/integration/ -v --timeout=120

lint:
	ruff check services/ shared/ tests/
	mypy services/ shared/ --ignore-missing-imports

format:
	ruff format services/ shared/ tests/

setup-hdfs:
	bash scripts/setup_hdfs.sh

seed:
	python scripts/seed_demo_data.py
