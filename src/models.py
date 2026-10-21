"""Data models: account statuses, currencies, owner and account classes."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Optional, Union

from exceptions import (
    AccountClosedError,
    AccountFrozenError,
    InsufficientFundsError,
    InvalidOperationError,
)
from utils import (
    Amount,
    format_money,
    generate_short_id,
    mask_account_id,
    to_money,
    validate_account_id,
)


class AccountStatus(Enum):
    ACTIVE = "active"
    FROZEN = "frozen"
    CLOSED = "closed"


class Currency(Enum):
    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"
    KZT = "KZT"
    CNY = "CNY"

    @classmethod
    def parse(cls, value: Union["Currency", str]) -> "Currency":
        """Accept a Currency member or a case-insensitive code such as 'usd'."""
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).upper())
        except ValueError:
            allowed = ", ".join(c.value for c in cls)
            raise InvalidOperationError(
                f"Unsupported currency: {value!r}. Allowed: {allowed}"
            ) from None


@dataclass(frozen=True)
class Owner:
    """Account owner details."""

    full_name: str
    email: Optional[str] = None

    def __post_init__(self):
        if not isinstance(self.full_name, str) or not self.full_name.strip():
            raise InvalidOperationError("Owner name cannot be empty")
        if self.email is not None and "@" not in self.email:
            raise InvalidOperationError(f"Invalid email: {self.email!r}")

    def __str__(self) -> str:
        return self.full_name


class AbstractAccount(ABC):
    """Base account: ID, owner, protected balance and status.

    Holds the logic shared by every account type (status guard and
    status transitions); subclasses define how money moves.
    """

    account_type = "Account"

    # Status changes allowed from each status; CLOSED is terminal.
    _ALLOWED_TRANSITIONS: dict[AccountStatus, frozenset[AccountStatus]] = {
        AccountStatus.ACTIVE: frozenset({AccountStatus.FROZEN, AccountStatus.CLOSED}),
        AccountStatus.FROZEN: frozenset({AccountStatus.ACTIVE, AccountStatus.CLOSED}),
        AccountStatus.CLOSED: frozenset(),
    }

    # Statuses that block money operations, mapped to the error they raise.
    _BLOCKING_STATUSES: dict[AccountStatus, type[Exception]] = {
        AccountStatus.FROZEN: AccountFrozenError,
        AccountStatus.CLOSED: AccountClosedError,
    }

    def __init__(
        self,
        owner: Owner,
        account_id: str,
        balance: Amount = 0,
        status: AccountStatus = AccountStatus.ACTIVE,
    ):
        if not isinstance(owner, Owner):
            raise InvalidOperationError("owner must be an Owner instance")
        if not isinstance(status, AccountStatus):
            raise InvalidOperationError(f"Unknown account status: {status!r}")
        self._account_id = validate_account_id(account_id)
        self._owner = owner
        self._balance = to_money(balance, allow_zero=True)
        self._status = status

    @property
    def account_id(self) -> str:
        return self._account_id

    @property
    def owner(self) -> Owner:
        return self._owner

    @property
    def balance(self) -> Decimal:
        # Read-only: the balance changes only through deposit/withdraw
        return self._balance

    @property
    def status(self) -> AccountStatus:
        return self._status

    def _ensure_operable(self) -> None:
        """Raise if the current status does not allow money operations."""
        error = self._BLOCKING_STATUSES.get(self._status)
        if error is not None:
            raise error(self._account_id)

    def _change_status(self, new_status: AccountStatus) -> None:
        if self._status is AccountStatus.CLOSED:
            raise AccountClosedError(self._account_id)
        if new_status not in self._ALLOWED_TRANSITIONS[self._status]:
            raise InvalidOperationError(
                f"Cannot change status from {self._status.value} to {new_status.value}"
            )
        self._status = new_status

    @abstractmethod
    def deposit(self, amount: Amount) -> Decimal:
        """Add money to the account and return the new balance."""

    @abstractmethod
    def withdraw(self, amount: Amount) -> Decimal:
        """Take money from the account and return the new balance."""

    @abstractmethod
    def get_account_info(self) -> dict[str, Any]:
        """Return account details as a dictionary."""


class BankAccount(AbstractAccount):
    """A regular single-currency bank account."""

    account_type = "Bank account"

    def __init__(
        self,
        owner: Owner,
        account_id: Optional[str] = None,
        balance: Amount = 0,
        currency: Union[Currency, str] = Currency.USD,
        status: AccountStatus = AccountStatus.ACTIVE,
        *,
        id_generator: Callable[[], str] = generate_short_id,
    ):
        # The ID generator is injected so callers and tests can supply their own
        if account_id is None:
            account_id = id_generator()
        super().__init__(owner, account_id, balance, status)
        self._currency = Currency.parse(currency)

    @property
    def currency(self) -> Currency:
        return self._currency

    def deposit(self, amount: Amount) -> Decimal:
        self._ensure_operable()
        self._balance += to_money(amount)
        return self._balance

    def withdraw(self, amount: Amount) -> Decimal:
        self._ensure_operable()
        amount = to_money(amount)
        return self._debit(amount, available=self._balance)

    def _debit(self, total: Decimal, available: Decimal) -> Decimal:
        """Subtract an already validated total if it fits into the available funds.

        Subclasses decide what "available" means (minimum balance, overdraft,
        free cash) and reuse this single place that actually moves the money.
        """
        if total > available:
            raise InsufficientFundsError(available, total, self._currency.value)
        self._balance -= total
        return self._balance

    def freeze(self) -> None:
        self._change_status(AccountStatus.FROZEN)

    def unfreeze(self) -> None:
        self._change_status(AccountStatus.ACTIVE)

    def close(self) -> None:
        # Money must be withdrawn first so it cannot get stuck on a closed account
        if self._status is not AccountStatus.CLOSED and self._balance != 0:
            raise InvalidOperationError(
                f"Cannot close an account with a non-zero balance "
                f"({format_money(self._balance)} {self._currency.value})"
            )
        self._change_status(AccountStatus.CLOSED)

    def get_account_info(self) -> dict[str, Any]:
        return {
            "type": self.account_type,
            "account_id": self._account_id,
            "owner": self._owner.full_name,
            "email": self._owner.email,
            "status": self._status.value,
            "balance": self._balance,
            "currency": self._currency.value,
        }

    def __str__(self) -> str:
        return (
            f"{self.account_type} | Client: {self._owner} | "
            f"No.: {mask_account_id(self._account_id)} | "
            f"Status: {self._status.value} | "
            f"Balance: {format_money(self._balance)} {self._currency.value}"
        )

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(owner={self._owner!r}, "
            f"account_id={self._account_id!r}, balance={self._balance!r}, "
            f"currency={self._currency}, status={self._status})"
        )
