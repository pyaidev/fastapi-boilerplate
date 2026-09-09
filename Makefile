.PHONY: init up down logs test test-local unit lint typecheck lock backup migrate check
init:
	python3 scripts/init_env.py
up: init
	docker compose up --build -d --wait
down:
	docker compose down
logs:
	docker compose logs --tail=100 -f api worker
test:
	docker compose --profile test run --build --rm test
test-local:
	uv run --frozen python -m scripts.test --local
unit:
	uv run --frozen pytest -m "not integration"
lint:
	uv run --frozen ruff check .
	uv run --frozen ruff format --check .
typecheck:
	uv run --frozen mypy app
lock:
	uv lock
	uv export --frozen --no-dev --format requirements-txt --output-file requirements/base.txt --quiet
	uv export --frozen --format requirements-txt --output-file requirements/dev.txt --quiet
backup:
	sh scripts/backup.sh
migrate:
	docker compose run --rm migrate
check: lint typecheck
