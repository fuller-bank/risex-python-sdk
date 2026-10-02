from decimal import Decimal, localcontext

import pytest

from risex import PrecisionError, from_steps, from_wei, to_steps, to_wei


def test_exact_conversions_under_small_decimal_context():
    with localcontext() as context:
        context.prec = 5
        price = Decimal("63126.084821834306372811")
        wei = 63126084821834306372811
        assert to_wei(price) == wei
        assert from_wei(wei) == price
        assert from_steps(63126084821834306372811, Decimal("1e-18")) == price
        assert to_steps(Decimal("123456789.123456789"), Decimal("0.000000001")) == (
            123456789123456789
        )


@pytest.mark.parametrize("value", ["0.0000011", "NaN", "Infinity", "-1"])
def test_unrepresentable_values_are_rejected(value):
    with pytest.raises(PrecisionError):
        to_steps(Decimal(value), Decimal("0.000001"))


def test_no_float_coercion():
    with pytest.raises(TypeError):
        to_steps(0.1, Decimal("0.1"))


@pytest.mark.parametrize("value", ["1.1", True, "Infinity", "1e18"])
def test_wei_requires_an_integer(value):
    with pytest.raises(PrecisionError):
        from_wei(value)


def test_signed_wei_and_zero():
    assert from_wei("-1000000000000000000") == Decimal("-1")
    assert to_steps(Decimal("0"), Decimal("0.1")) == 0
    assert from_steps(0, Decimal("0.1")) == 0


def test_market_conversion_validates_minimum(markets_payload):
    from risex import MarketsResponse

    market = MarketsResponse.model_validate(markets_payload["data"]).markets[0]
    assert market.config.quantity_to_steps(Decimal("0.001")) == 1000
    assert market.config.price_to_ticks(Decimal("63218.5")) == 632185
    with pytest.raises(PrecisionError):
        market.config.quantity_to_steps(Decimal("0.000001"))
    with pytest.raises(PrecisionError):
        market.config.price_to_ticks(Decimal("63218.51"))
