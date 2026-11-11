from decimal import Decimal

import pytest

from task_platform.command_schemas import CommandError, parse_command


@pytest.mark.parametrize("body", [b'{"input":"\\u0000"}', b'{"\\u0000":1}'])
def test_postgres_unsupported_nul_is_rejected_at_boundary(body: bytes) -> None:
    with pytest.raises(CommandError) as error:
        parse_command(body)
    assert error.value.status == 422


@pytest.mark.parametrize("exponent", [b"+999999999999999999999", b"-999999999999999999999"])
def test_unrepresentable_decimal_exponent_is_rejected_at_boundary(exponent: bytes) -> None:
    with pytest.raises(CommandError) as error:
        parse_command(b'{"limit":1e' + exponent + b"}")
    assert error.value.status == 422


@pytest.mark.parametrize(
    "left,right",
    [
        (b'{"a":1,"b":2}', b'{"b":2.0,"a":1e0}'),
        (b'{"x":-0.0}', b'{"x":0}'),
        (b'{"x":0.0001}', b'{"x":1e-4}'),
    ],
)
def test_decimal_canonicalization_is_exact(left: bytes, right: bytes) -> None:
    assert parse_command(left)[1] == parse_command(right)[1]


@pytest.mark.parametrize(
    "left,right",
    [
        (b"{}", b'{"x":null}'),
        (b'{"x":1}', b'{"x":true}'),
        (b'{"x":[1,2]}', b'{"x":[2,1]}'),
        (b'{"x":12345678901234567890123456789}', b'{"x":12345678901234567890123456788}'),
    ],
)
def test_distinct_commands_do_not_alias(left: bytes, right: bytes) -> None:
    assert parse_command(left)[1] != parse_command(right)[1]


def test_fractional_values_remain_decimal_without_binary_float() -> None:
    value, _ = parse_command(b'{"cost_micro_usd":0.1}')
    assert value == {"cost_micro_usd": Decimal("0.1")}
