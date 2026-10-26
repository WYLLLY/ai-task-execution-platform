"""Offline tests of the separately authorized live-smoke controller."""

import json
from pathlib import Path
from types import TracebackType
from typing import Self

import pytest

from task_platform import provider_commands
from task_platform.execution import Permit
from task_platform.responses import parse_output, validate_profile
from task_platform.transport import Outcome
from tests.unit.test_responses import profile_data, response


@pytest.mark.parametrize("final_report", [True, False])
async def test_smoke_requires_bounded_stateless_tool_roundtrip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    final_report: bool,
) -> None:
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps(profile_data()))
    monkeypatch.setenv("OPENAI_PROFILE_FILE", str(profile))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    monkeypatch.delenv("OPENAI_API_KEY_FILE", raising=False)
    monkeypatch.setenv("OPENAI_SMOKE_AUTHORIZED", "yes")
    monkeypatch.setenv("OPENAI_SMOKE_CAP_MICRO_USD", "50000")
    calls: list[dict[str, object]] = []

    class FakeTransport:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(
            self,
            kind: type[BaseException] | None,
            value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            pass

        async def send(self, permit: Permit) -> Outcome:
            calls.append(json.loads(permit.payload))
            raw = response()
            if len(calls) == 2 and final_report:
                raw["output"] = [
                    {
                        "type": "message",
                        "id": "msg_1",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "type": "report",
                                        "title": "Synthetic",
                                        "summary": "Three colors",
                                        "citations": [],
                                    }
                                ),
                                "annotations": [],
                            }
                        ],
                    }
                ]
            return parse_output(validate_profile(profile_data()), raw)

    monkeypatch.setattr(provider_commands, "ResponsesTransport", FakeTransport)
    if final_report:
        await provider_commands.smoke_openai()
        assert '"status": "PASS"' in capsys.readouterr().out
    else:
        with pytest.raises(SystemExit):
            await provider_commands.smoke_openai()
        assert '"status": "FAIL"' in capsys.readouterr().out
    assert len(calls) == 2
    assert calls[0]["tool_choice"] == {"type": "function", "name": "search_documents"}
    assert calls[1]["tool_choice"] == "none"
    inputs = calls[1]["input"]
    assert isinstance(inputs, list)
    assert inputs[1:3] == response()["output"]
    assert inputs[3]["call_id"] == "call_1"


async def test_first_turn_overrun_cannot_spend_the_second_turn_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_SMOKE_AUTHORIZED", "yes")
    monkeypatch.setenv("OPENAI_SMOKE_CAP_MICRO_USD", "50000")
    monkeypatch.setenv("OPENAI_PROFILE_FILE", "unused-by-fake-loader")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    monkeypatch.delenv("OPENAI_API_KEY_FILE", raising=False)
    monkeypatch.setattr(provider_commands, "load_profile", lambda: validate_profile(profile_data()))
    calls: list[Permit] = []

    class FakeTransport:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(
            self,
            kind: type[BaseException] | None,
            value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            pass

        async def send(self, permit: Permit) -> Outcome:
            calls.append(permit)
            return parse_output(validate_profile(profile_data()), response()).model_copy(
                update={"cost": 49999}
            )

    monkeypatch.setattr(provider_commands, "ResponsesTransport", FakeTransport)
    with pytest.raises(SystemExit):
        await provider_commands.smoke_openai()
    assert len(calls) == 1
