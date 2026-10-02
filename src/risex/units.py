"""Exact conversions independent of the application's Decimal context."""

from __future__ import annotations

import re
from decimal import Decimal
from fractions import Fraction

from .exceptions import PrecisionError


def _decimal(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal; floats are not supported")
    if not value.is_finite():
        raise PrecisionError(f"{name} must be finite")


def to_steps(value: Decimal, step: Decimal) -> int:
    """Return exact nonnegative step/tick count; never round."""
    _decimal(value, "value")
    _decimal(step, "step")
    if value < 0 or step <= 0:
        raise PrecisionError("value must be nonnegative and step must be positive")
    ratio = Fraction(value) / Fraction(step)
    if ratio.denominator != 1:
        raise PrecisionError(f"{value} is not an exact multiple of {step}")
    return ratio.numerator


def from_steps(count: int, step: Decimal) -> Decimal:
    """Multiply by an integer without Decimal context rounding."""
    if type(count) is not int or count < 0:
        raise ValueError("count must be a nonnegative integer")
    _decimal(step, "step")
    if step <= 0:
        raise PrecisionError("step must be positive")
    parts = step.as_tuple()
    assert isinstance(parts.exponent, int)  # Nonfinite values were rejected above.
    coefficient = int("".join(map(str, parts.digits))) * count
    return Decimal((0, tuple(map(int, str(coefficient))), parts.exponent))


def to_wei(value: Decimal) -> int:
    return to_steps(value, Decimal("1e-18"))


def from_wei(value: str | int) -> Decimal:
    """Decode a signed integer with 18 decimals exactly."""
    if isinstance(value, bool) or not re.fullmatch(r"-?[0-9]+", str(value)):
        raise PrecisionError("wei value must be an integer")
    integer = int(value)
    return Decimal((int(integer < 0), tuple(map(int, str(abs(integer)))), -18))
