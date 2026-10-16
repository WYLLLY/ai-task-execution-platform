import json
import logging
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from task_platform.config import Settings, secret_setting
from task_platform.identity import key_digest, new_key
from task_platform.observability import JSONFormatter


def test_tokens_have_independent_high_entropy_secrets() -> None:
    tokens = {new_key() for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(token) == 47 and token.startswith("atp_") for token in tokens)
    assert all(token not in key_digest(token) for token in tokens)
    assert len({key_digest(token) for token in tokens}) == 100


def test_settings_hide_credentials_and_validate_connection_budget() -> None:
    url = "postgresql+psycopg://user:SYNTHETIC_CANARY@db/atp"
    settings = Settings(role="api", database_url=SecretStr(url), execution_epoch=uuid4())
    assert "SYNTHETIC_CANARY" not in repr(settings)
    assert "SYNTHETIC_CANARY" not in settings.model_dump_json()
    with pytest.raises(ValidationError) as error:
        Settings(
            role="api", database_url=SecretStr(url), execution_epoch=uuid4(), db_max_connections=37
        )
    assert "SYNTHETIC_CANARY" not in str(error.value)


@pytest.mark.parametrize("url", ["sqlite://", "postgresql://user@db/atp", "https://db"])
def test_postgres_driver_is_required(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(role="api", database_url=SecretStr(url), execution_epoch=uuid4())


def test_secret_file_and_environment_are_mutually_exclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "secret"
    path.write_text("CANARY\n")
    monkeypatch.setenv("M1_SECRET_FILE", str(path))
    assert secret_setting("M1_SECRET").get_secret_value() == "CANARY"
    monkeypatch.setenv("M1_SECRET", "OTHER")
    with pytest.raises(ValueError):
        secret_setting("M1_SECRET")
    monkeypatch.delenv("M1_SECRET")
    monkeypatch.delenv("M1_SECRET_FILE")
    with pytest.raises(ValueError):
        secret_setting("M1_SECRET")


def test_logging_omits_exception_message_args_and_arbitrary_extras() -> None:
    record = logging.LogRecord(
        "SENSITIVE_LOGGER", logging.ERROR, "SENSITIVE_PATH", 1, "secret %s", ("CANARY",), None
    )
    record.exc_text = "SENSITIVE_TRACEBACK"
    record.authorization = "SENSITIVE_BEARER"
    record.event = "request.failed"
    record.status = 500
    rendered = JSONFormatter().format(record)
    assert "CANARY" not in rendered and "SENSITIVE" not in rendered
    assert json.loads(rendered)["event"] == "request.failed"


def test_unrecognized_log_events_are_not_rendered() -> None:
    record = logging.LogRecord("test", logging.INFO, "", 0, "CANARY", (), None)
    record.event = "CANARY"
    record.status = "CANARY"
    assert "CANARY" not in JSONFormatter().format(record)


def test_secret_generation_preserves_existing_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.dev_secrets import main

    monkeypatch.chdir(tmp_path)
    target = tmp_path / ".env"
    target.write_text("API_PORT=12345\nEXECUTION_EPOCH=preserved\n")
    before = target.read_bytes()
    with pytest.raises(SystemExit):
        main()
    assert target.read_bytes() == before
    assert not (tmp_path / ".secrets").exists()


def test_secret_generation_isolated_and_non_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.dev_secrets import main

    monkeypatch.chdir(tmp_path)
    main()
    root = tmp_path / ".secrets"
    assert root.stat().st_mode & 0o777 == 0o700
    passwords = [p.read_text() for p in root.glob("*_password")]
    assert len(passwords) == len(set(passwords)) == 6
    existing = {p.name: p.read_bytes() for p in root.iterdir()}
    with pytest.raises(SystemExit):
        main()
    assert existing == {p.name: p.read_bytes() for p in root.iterdir()}
