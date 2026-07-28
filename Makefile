.PHONY: dev up down logs test lint format migrate makemigrations superuser

dev:
	docker compose -f deploy/compose.yaml up --build

up:
	docker compose -f deploy/compose.yaml up -d --build

down:
	docker compose -f deploy/compose.yaml down

logs:
	docker compose -f deploy/compose.yaml logs -f

test:
	docker compose -f deploy/compose.yaml run --rm backend pytest

lint:
	docker compose -f deploy/compose.yaml run --rm backend ruff check .

format:
	docker compose -f deploy/compose.yaml run --rm backend ruff format .

migrate:
	docker compose -f deploy/compose.yaml run --rm backend python manage.py migrate

makemigrations:
	docker compose -f deploy/compose.yaml run --rm backend python manage.py makemigrations

superuser:
	docker compose -f deploy/compose.yaml run --rm backend python manage.py createsuperuser
