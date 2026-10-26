"""Bounded JSON parsing and versioned canonicalization at the command boundary."""

import hashlib
import json
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

MAX_BODY = 1024 * 1024


class CommandError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail)


def invalid() -> CommandError:
    return CommandError(422, "Invalid request")


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise invalid()
        result[key] = value
    return result


def _nonfinite(_: str) -> None:
    raise invalid()


def _canonical(value: object, depth: int = 0) -> str:
    if depth > 32:
        raise invalid()
    if value is None or isinstance(value, bool):
        return json.dumps(value)
    if isinstance(value, str):
        if "\x00" in value:
            raise invalid()
        value.encode("utf-8", errors="strict")
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, Decimal):
        if (
            not value.is_finite()
            or len(value.as_tuple().digits) > 64
            or abs(int(value.as_tuple().exponent)) > 100
        ):
            raise invalid()
        if not value:
            return "0"
        encoded = format(value, "f")
        return encoded.rstrip("0").rstrip(".") if "." in encoded else encoded
    if isinstance(value, list):
        return "[" + ",".join(_canonical(item, depth + 1) for item in value) + "]"
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                _canonical(key, depth + 1) + ":" + _canonical(value[key], depth + 1)
                for key in sorted(value)
            )
            + "}"
        )
    raise invalid()


def _schema_numbers(value: object) -> object:
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    if isinstance(value, list):
        return [_schema_numbers(item) for item in value]
    if isinstance(value, dict):
        return {key: _schema_numbers(item) for key, item in value.items()}
    return value


def parse_command(body: bytes) -> tuple[object, str]:
    if len(body) > MAX_BODY:
        raise CommandError(413, "Payload too large")
    try:
        value = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_int=Decimal,
            parse_float=Decimal,
            parse_constant=_nonfinite,
        )
        canonical = _canonical(value)
    except ValueError, UnicodeError, RecursionError:
        raise invalid() from None
    fingerprint = hashlib.sha256(("v1:" + canonical).encode("utf-8")).hexdigest()
    return _schema_numbers(value), fingerprint


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class WorkflowReference(StrictSchema):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    version: Annotated[str, Field(min_length=1, max_length=40)]


class DocumentReference(StrictSchema):
    id: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    version: int = Field(ge=1)


class TaskInput(StrictSchema):
    documents: list[DocumentReference] = Field(default_factory=list, max_length=20)
    texts: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=MAX_BODY)]],
        Field(min_length=1, max_length=20),
    ]


class TaskLimits(StrictSchema):
    deadline_seconds: int = Field(default=900, ge=1, le=900)
    llm_calls: int = Field(default=8, ge=0, le=8)
    tool_calls: int = Field(default=8, ge=0, le=8)
    tokens: int = Field(default=40000, ge=0, le=40000)
    cost_micro_usd: int = Field(default=1000000, ge=0, le=1000000)


class CreateTask(StrictSchema):
    workflow: WorkflowReference
    input: TaskInput
    limits: TaskLimits = Field(default_factory=TaskLimits)


class CancelTask(StrictSchema):
    expected_task_version: int = Field(gt=0)
