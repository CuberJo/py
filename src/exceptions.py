"""Domain exceptions raised by account and bank operations."""

from decimal import Decimal
from typing import Optional


class BankError(Exception):
    """Base class for every error of the bank model."""


class AccountError(BankError):
    """Base class for every account-related error."""


class AccountFrozenError(AccountError):
    """The operation is rejected because the account is frozen."""

    def __init__(self, account_id: str):
        super().__init__(f"Account {account_id} is frozen, operations are not allowed")
        self.account_id = account_id


class AccountClosedError(AccountError):
    """The operation is rejected because the account is closed."""

    def __init__(self, account_id: str):
        super().__init__(f"Account {account_id} is closed, operations are not allowed")
        self.account_id = account_id


class InvalidOperationError(AccountError):
    """Invalid input data or an operation that is not allowed."""


class InsufficientFundsError(AccountError):
    """The available funds are too low for the requested operation."""

    def __init__(self, available: Decimal, requested: Decimal, currency: str):
        super().__init__(
            f"Insufficient funds: available {available:.2f} {currency}, "
            f"requested {requested:.2f} {currency}"
        )
        self.available = available
        self.requested = requested


class LimitExceededError(AccountError):
    """The amount is above the per-operation limit of the account."""

    def __init__(self, limit: Decimal, requested: Decimal, currency: str):
        super().__init__(
            f"Operation limit exceeded: limit {limit:.2f} {currency}, "
            f"requested {requested:.2f} {currency}"
        )
        self.limit = limit
        self.requested = requested


class AgeRestrictionError(InvalidOperationError):
    """The client is younger than the minimum age."""

    def __init__(self, age: int, minimum: int):
        super().__init__(f"Client must be at least {minimum} years old, got {age}")
        self.age = age
        self.minimum = minimum


class ClientNotFoundError(BankError):
    """No client with this ID is registered in the bank."""

    def __init__(self, client_id: str):
        super().__init__(f"Client {client_id} not found")
        self.client_id = client_id


class AccountNotFoundError(BankError):
    """No account with this number is registered in the bank."""

    def __init__(self, account_id: str):
        super().__init__(f"Account {account_id} not found")
        self.account_id = account_id


class AuthenticationError(BankError):
    """Wrong client ID or PIN."""

    def __init__(self, attempts_left: Optional[int] = None):
        message = "Wrong client ID or PIN"
        if attempts_left is not None:
            message += f", attempts left: {attempts_left}"
        super().__init__(message)
        self.attempts_left = attempts_left


class ClientBlockedError(BankError):
    """The client is blocked, for example after too many failed logins."""

    def __init__(self, client_id: str):
        super().__init__(f"Client {client_id} is blocked, operations are not allowed")
        self.client_id = client_id


class OperationTimeError(BankError):
    """The operation is not allowed at this time of day."""
