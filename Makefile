UV ?= uv
COMPOSE ?= docker compose
TEST_COMPOSE = $(COMPOSE) -p atp-m1-test -f compose.yaml -f compose.demo.yaml -f compose.proxy.yaml

.PHONY: sync lint format typecheck test-unit test-integration test-commands test-concurrency test-execution test-failures smoke-compose dev-secrets
sync:
	$(UV) sync --frozen
lint:
	$(UV) run --frozen ruff check .
	$(UV) run --frozen ruff format --check .
format:
	$(UV) run --frozen ruff format .
typecheck:
	$(UV) run --frozen mypy
prepare-tokenizer:
	$(UV) run --frozen python -c 'import tiktoken; tiktoken.get_encoding("o200k_base")'
test-unit:
	$(UV) run --frozen pytest tests/unit
dev-secrets:
	$(UV) run --frozen python scripts/dev_secrets.py
	$(UV) run --frozen python scripts/demo_secrets.py
test-integration:
	$(TEST_COMPOSE) up --build --wait api demo proxy
	$(TEST_COMPOSE) build test
	$(UV) run --frozen python scripts/run_verification.py tests/integration
smoke-compose:
	$(UV) run --frozen python scripts/smoke_compose.py

# Focused M2 gates; use the disposable stack prepared by test-integration.
test-commands:
	$(TEST_COMPOSE) run --rm --no-deps test pytest -p no:cacheprovider tests/integration/test_commands.py tests/integration/test_command_regressions.py tests/integration/test_command_resilience.py tests/integration/test_task_states.py tests/integration/test_state_matrix.py tests/integration/test_command_cli.py
test-concurrency:
	$(TEST_COMPOSE) run --rm --no-deps test pytest -p no:cacheprovider tests/integration -k 'concurrent or serialization or waits or waiting or revocation_committed or lock_wait or lock_contention or skip_locked'

# M3 gates: installed test image, real PostgreSQL and an independent HTTP simulator process.
test-execution:
	$(TEST_COMPOSE) run --rm --no-deps test pytest -p no:cacheprovider tests/integration/test_execution.py tests/integration/test_execution_calls.py tests/integration/test_execution_resolution.py tests/integration/test_execution_schema.py tests/integration/test_execution_worker.py
test-failures:
	$(TEST_COMPOSE) run --rm --no-deps test pytest -p no:cacheprovider tests/integration/test_execution_failures.py tests/integration/test_execution_transport.py

# M4: simulated agent workflow and independent PostgreSQL report service.
.PHONY: test-workflows test-tools test-e2e
test-workflows:
	$(TEST_COMPOSE) run --rm --no-deps test pytest -p no:cacheprovider tests/integration/test_workflows.py tests/integration/test_workflow_boundaries.py tests/integration/test_workflow_reports.py tests/integration/test_workflow_corpus.py
test-tools:
	$(TEST_COMPOSE) run --rm --no-deps test pytest -p no:cacheprovider tests/unit/test_agent_protocol.py tests/integration/test_tools_demo.py
test-e2e:
	$(TEST_COMPOSE) run --rm --no-deps test pytest -p no:cacheprovider tests/integration/test_workflow_e2e.py

# M5 uses synthetic Responses fixtures and real PostgreSQL accounting in ordinary CI.
.PHONY: test-accounting test-provider-contracts smoke-openai
test-accounting:
	$(TEST_COMPOSE) run --rm --no-deps test pytest -p no:cacheprovider tests/integration/test_accounting_admin.py tests/integration/test_accounting_schema.py tests/integration/test_provider_capacity.py tests/integration/test_execution_calls.py
test-provider-contracts:
	$(TEST_COMPOSE) run --rm --no-deps test pytest -p no:cacheprovider tests/unit/test_responses.py tests/unit/test_provider_cli.py tests/unit/test_openai_smoke.py tests/integration/test_provider_contracts.py tests/integration/test_responses_workflow.py tests/integration/test_provider_e2e.py
smoke-openai:
	$(UV) run --frozen atp smoke-openai

# M6: real TCP/proxy, PostgreSQL and externally controlled Redis outage.
.PHONY: test-sse test-degraded test-observability
test-sse:
	$(TEST_COMPOSE) run --rm --no-deps test pytest -p no:cacheprovider tests/unit/test_event_cursor.py tests/unit/test_sse_stream.py tests/unit/test_sse_tcp_lifetime.py tests/unit/test_event_hub.py tests/integration/test_event_reads.py tests/integration/test_sse.py tests/integration/test_sse_network.py tests/integration/test_notifications.py tests/integration/test_m6_projections.py
test-degraded:
	$(UV) run --frozen python scripts/run_verification.py tests/unit/test_degraded_readiness.py tests/integration/test_degraded.py
test-observability:
	$(TEST_COMPOSE) run --rm --no-deps test pytest -p no:cacheprovider tests/unit/test_operations.py tests/unit/test_telemetry.py tests/integration/test_observability.py

# M7: ordinary CI stays synthetic. The full reference campaign is explicit and long.
.PHONY: test-retention test-restore test-delivery test-load-contracts test-load verify-release-image
test-retention:
	$(TEST_COMPOSE) run --rm --no-deps test pytest -p no:cacheprovider tests/unit/test_retention_policy.py tests/integration/test_retention.py
test-restore:
	$(TEST_COMPOSE) run --rm --no-deps test pytest -p no:cacheprovider tests/unit/test_backup_artifact.py tests/unit/test_recovery_cli.py tests/unit/test_system_status.py tests/integration/test_restore.py tests/integration/test_restore_races.py
	$(UV) run --frozen python -m scripts.run_restore_drill
test-delivery:
	$(TEST_COMPOSE) run --rm --no-deps test pytest -p no:cacheprovider tests/integration/test_delivery.py tests/integration/test_delivery_failure.py
test-load-contracts:
	$(TEST_COMPOSE) run --rm --no-deps test pytest -p no:cacheprovider tests/unit/test_load_metrics.py tests/unit/test_load_orchestration.py tests/unit/test_semantic_fixtures.py
test-load:
	$(UV) run --frozen python -m scripts.run_load_profile
verify-release-image:
	$(UV) run --frozen python scripts/verify_release_image.py
