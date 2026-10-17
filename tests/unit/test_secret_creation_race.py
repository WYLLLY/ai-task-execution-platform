import runpy
import secrets
from pathlib import Path

import pytest

generate = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/dev_secrets.py"))[
    "main"
]


def test_environment_created_during_generation_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    original = secrets.token_hex

    def concurrent_writer(nbytes: int) -> str:
        Path(".env").write_text("EXISTING_OPERATOR_CONFIGURATION=preserve\n")
        return original(nbytes)

    monkeypatch.setattr(secrets, "token_hex", concurrent_writer)
    with pytest.raises(SystemExit):
        generate()
    assert Path(".env").read_text() == "EXISTING_OPERATOR_CONFIGURATION=preserve\n"


def test_dangling_environment_symlink_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "operator-config"
    Path(".env").symlink_to(target)
    with pytest.raises(SystemExit):
        generate()
    assert Path(".env").is_symlink()
    assert not target.exists()
