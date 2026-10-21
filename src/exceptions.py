"""Domain exceptions raised by account operations."""

from decimal import Decimal


class AccountError(Exception):
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
