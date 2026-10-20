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
    """The balance is too low for the requested withdrawal."""

    def __init__(self, balance: Decimal, requested: Decimal, currency: str):
        super().__init__(
            f"Insufficient funds: balance {balance:.2f} {currency}, "
            f"requested {requested:.2f} {currency}"
        )
        self.balance = balance
        self.requested = requested
