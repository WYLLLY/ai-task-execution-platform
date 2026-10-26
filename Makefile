UV ?= uv
COMPOSE ?= docker compose
TEST_COMPOSE = $(COMPOSE) -p atp-m1-test

.PHONY: sync lint format typecheck test-unit test-integration test-commands test-concurrency smoke-compose dev-secrets
sync:
	$(UV) sync --frozen
lint:
	$(UV) run --frozen ruff check .
	$(UV) run --frozen ruff format --check .
format:
	$(UV) run --frozen ruff format .
typecheck:
	$(UV) run --frozen mypy
test-unit:
	$(UV) run --frozen pytest tests/unit
dev-secrets:
	$(UV) run --frozen python scripts/dev_secrets.py
test-integration:
	$(TEST_COMPOSE) up --build --wait api
	$(TEST_COMPOSE) build test
	$(TEST_COMPOSE) run --rm --no-deps test pytest -p no:cacheprovider tests/integration
smoke-compose:
	$(UV) run --frozen python scripts/smoke_compose.py

# Focused M2 gates; use the disposable stack prepared by test-integration.
test-commands:
	$(TEST_COMPOSE) run --rm --no-deps test pytest -p no:cacheprovider tests/integration/test_commands.py tests/integration/test_command_regressions.py tests/integration/test_command_resilience.py tests/integration/test_task_states.py tests/integration/test_state_matrix.py tests/integration/test_command_cli.py
test-concurrency:
	$(TEST_COMPOSE) run --rm --no-deps test pytest -p no:cacheprovider tests/integration -k 'concurrent or serialization or waits or waiting or revocation_committed'
