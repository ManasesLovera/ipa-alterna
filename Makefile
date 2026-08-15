.DEFAULT_GOAL := help
.PHONY: help up down logs shell migrate revision lint format typecheck test \
        test-integration seed worker api web

COMPOSE ?= docker compose
UV ?= uv

help:  ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

up:  ## Start infrastructure and application services
	$(COMPOSE) up -d

down:  ## Stop services and remove volumes
	$(COMPOSE) down -v

logs:  ## Tail logs for all services
	$(COMPOSE) logs -f --tail=200

shell:  ## Open a shell inside the api container
	$(COMPOSE) exec api /bin/bash

migrate:  ## Apply all Alembic migrations
	$(UV) run alembic upgrade head

revision:  ## Autogenerate a migration: make revision m="add documents"
	$(UV) run alembic revision --autogenerate -m "$(m)"

lint:  ## Run ruff checks
	$(UV) run ruff check src tests scripts

format:  ## Format the codebase with ruff
	$(UV) run ruff format src tests scripts
	$(UV) run ruff check --fix src tests scripts

typecheck:  ## Run mypy over the package
	$(UV) run mypy src

test:  ## Run unit tests
	$(UV) run pytest tests/unit -v

test-integration:  ## Run integration tests (requires `make up`)
	$(UV) run pytest tests/integration -v

seed:  ## Load local development seed data
	$(UV) run python -m scripts.seed

worker:  ## Run a Celery worker locally
	$(UV) run celery -A ipa.pipeline.celery_app worker -l info

api:  ## Run the API locally with reload
	$(UV) run uvicorn ipa.api.main:app --reload --port 8000

web:  ## Run the Next.js frontend
	$(COMPOSE) --profile full up -d web
