PYTHON ?= python
COMPOSE ?= docker compose

.PHONY: up down reset logs ps test lint migrate rebuild

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down --remove-orphans

reset:
	$(COMPOSE) down -v --remove-orphans

logs:
	$(COMPOSE) logs -f --tail=200

ps:
	$(COMPOSE) ps

migrate:
	$(COMPOSE) run --rm migrate

rebuild:
	$(COMPOSE) build --no-cache

test:
	PYTHONPATH=packages/contracts/src:. $(PYTHON) -m pytest -q

lint:
	PYTHONPATH=packages/contracts/src:. $(PYTHON) -m compileall apps libs packages/contracts/src tests