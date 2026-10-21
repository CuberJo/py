"""Helper functions: money validation, ID generation and formatting."""

import uuid
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Optional, Union

from exceptions import InvalidOperationError

Amount = Union[int, float, str, Decimal]
Rate = Union[int, float, str, Decimal]

CENT = Decimal("0.01")
SHORT_ID_LENGTH = 12
MIN_ACCOUNT_ID_LENGTH = 4


def to_money(value: Amount, *, allow_zero: bool = False) -> Decimal:
    """Convert a value to a money amount with cent precision.

    Rejects non-numeric values, bool, NaN/infinity, negative numbers,
    zero (unless allowed) and amounts more precise than one cent.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise InvalidOperationError(f"Amount must be a number, got: {value!r}")
    try:
        # Go through str() so that 0.1 does not become 0.1000000000000000055...
        amount = Decimal(str(value))
    except InvalidOperation:
        raise InvalidOperationError(f"Invalid amount: {value!r}") from None
    if not amount.is_finite():
        raise InvalidOperationError(f"Invalid amount: {value!r}")
    if amount < 0:
        raise InvalidOperationError(f"Amount cannot be negative: {value!r}")
    if amount == 0 and not allow_zero:
        raise InvalidOperationError("Amount must be greater than zero")
    if amount != amount.quantize(CENT):
        raise InvalidOperationError(f"Amount is more precise than one cent: {value!r}")
    return amount.quantize(CENT)


def round_money(value: Decimal) -> Decimal:
    """Round a calculated amount (interest, growth) to cents, banker's rounding."""
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def to_rate(
    value: Rate, *, minimum: Decimal = Decimal(0), maximum: Optional[Decimal] = None
) -> Decimal:
    """Convert a value to a rate given as a fraction (0.05 means 5%).

    Rejects non-numeric values, bool, NaN/infinity and values outside
    [minimum, maximum].
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise InvalidOperationError(f"Rate must be a number, got: {value!r}")
    try:
        rate = Decimal(str(value))
    except InvalidOperation:
        raise InvalidOperationError(f"Invalid rate: {value!r}") from None
    if not rate.is_finite():
        raise InvalidOperationError(f"Invalid rate: {value!r}")
    if rate < minimum or (maximum is not None and rate > maximum):
        upper = "" if maximum is None else f" and {maximum}"
        raise InvalidOperationError(f"Rate must be between {minimum}{upper}: {value!r}")
    return rate


def generate_short_id(length: int = SHORT_ID_LENGTH) -> str:
    """Return a short uppercase UUID-based account number."""
    return uuid.uuid4().hex[:length].upper()


def validate_account_id(account_id: str) -> str:
    """Return the stripped account number or raise if it is invalid."""
    if not isinstance(account_id, str) or not account_id.strip():
        raise InvalidOperationError("Account number must be a non-empty string")
    account_id = account_id.strip()
    if len(account_id) < MIN_ACCOUNT_ID_LENGTH:
        raise InvalidOperationError(
            f"Account number must be at least {MIN_ACCOUNT_ID_LENGTH} characters long"
        )
    return account_id


def mask_account_id(account_id: str) -> str:
    """Hide everything except the last 4 characters: '****1234'."""
    return f"****{account_id[-4:]}"


def format_money(amount: Decimal) -> str:
    """Format an amount with space-separated thousands: '1 000.00'."""
    return f"{amount:,.2f}".replace(",", " ")


def format_percent(rate: Decimal) -> str:
    """Format a fractional rate as a percentage: Decimal('0.015') -> '1.50%'."""
    return f"{rate * 100:.2f}%"
