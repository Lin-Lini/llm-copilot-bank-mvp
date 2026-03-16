PYTHON ?= python
COMPOSE ?= docker compose

.PHONY: up down reset logs ps test lint

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

reset:
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f --tail=200

ps:
	$(COMPOSE) ps

test:
	PYTHONPATH=packages/contracts/src:. $(PYTHON) -m pytest -q

lint:
	PYTHONPATH=packages/contracts/src:. $(PYTHON) -m compileall apps libs packages/contracts/src tests
