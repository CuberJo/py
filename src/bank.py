"""Bank: a registry of clients and accounts with login protection and security rules."""

from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from typing import Any, Callable, Optional, Union

from accounts import InvestmentAccount
from clients import Client
from exceptions import (
    AccountNotFoundError,
    AuthenticationError,
    ClientBlockedError,
    ClientNotFoundError,
    InvalidOperationError,
    OperationTimeError,
)
from models import AccountStatus, BankAccount, Currency
from utils import Amount, format_money, to_money


@dataclass(frozen=True)
class SuspiciousActivity:
    """A flagged event: when it happened, why, and who was involved."""

    timestamp: datetime
    reason: str
    client_id: Optional[str] = None
    account_id: Optional[str] = None


@dataclass(frozen=True)
class ClientRank:
    """One row of the clients ranking."""

    position: int
    client: Client
    total: Decimal


class Bank:
    """Manages clients and their accounts.

    Security rules:
    - ``MAX_LOGIN_ATTEMPTS`` wrong PINs in a row block the client;
    - account operations are not allowed from ``NIGHT_START`` to ``NIGHT_END``,
      except freezing, which protects the client and is always allowed;
    - failed logins for unknown IDs, blocking, night attempts and large
      operations are recorded as suspicious activity.
    """

    MAX_LOGIN_ATTEMPTS = 3
    NIGHT_START = time(0, 0)
    NIGHT_END = time(5, 0)
    # Deposits and withdrawals of at least this amount are flagged (in account currency)
    LARGE_OPERATION_THRESHOLD = Decimal("1000000.00")

    def __init__(self, name: str, *, clock: Callable[[], datetime] = datetime.now):
        # The clock is injected so tests and the demo can control the time of day
        if not isinstance(name, str) or not name.strip():
            raise InvalidOperationError("Bank name cannot be empty")
        self._name = name.strip()
        self._clock = clock
        self._clients: dict[str, Client] = {}
        self._accounts: dict[str, BankAccount] = {}
        self._account_owners: dict[str, str] = {}  # account_id -> client_id
        self._failed_logins: dict[str, int] = {}
        self._suspicious: list[SuspiciousActivity] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def clients(self) -> tuple[Client, ...]:
        return tuple(self._clients.values())

    # --- Clients ---

    def add_client(self, client: Client) -> Client:
        if not isinstance(client, Client):
            raise InvalidOperationError("client must be a Client instance")
        if client.client_id in self._clients:
            raise InvalidOperationError(f"Client {client.client_id} already exists")
        self._clients[client.client_id] = client
        return client

    def get_client(self, client_id: str) -> Client:
        client = self._clients.get(client_id)
        if client is None:
            raise ClientNotFoundError(client_id)
        return client

    def authenticate_client(self, client_id: str, pin: str) -> Client:
        """Return the client if the PIN is right.

        Every wrong PIN raises ``AuthenticationError`` with the attempts left;
        the last allowed one blocks the client and raises ``ClientBlockedError``.
        """
        client = self._clients.get(client_id)
        if client is None:
            # Same error as a wrong PIN, so the caller cannot probe which IDs exist
            self._flag("Login attempt with an unknown client ID", client_id=client_id)
            raise AuthenticationError()
        if client.is_blocked:
            self._flag("Login attempt while blocked", client_id=client_id)
            raise ClientBlockedError(client_id)
        if client.check_pin(pin):
            self._failed_logins.pop(client_id, None)
            return client

        failures = self._failed_logins.get(client_id, 0) + 1
        self._failed_logins[client_id] = failures
        if failures >= self.MAX_LOGIN_ATTEMPTS:
            client.block()
            self._failed_logins.pop(client_id)
            self._flag(f"Blocked after {failures} wrong PINs in a row", client_id=client_id)
            raise ClientBlockedError(client_id)
        raise AuthenticationError(self.MAX_LOGIN_ATTEMPTS - failures)

    def unblock_client(self, client_id: str) -> None:
        self.get_client(client_id).unblock()
        self._failed_logins.pop(client_id, None)

    # --- Accounts ---

    def open_account(
        self,
        client_id: str,
        account_class: type[BankAccount] = BankAccount,
        **kwargs: Any,
    ) -> BankAccount:
        """Open an account of ``account_class`` for the client.

        ``kwargs`` go to the account constructor (balance, currency, min_balance, ...);
        the owner is taken from the client.
        """
        self._ensure_working_hours("open an account", client_id=client_id)
        client = self._get_active_client(client_id)
        if not (isinstance(account_class, type) and issubclass(account_class, BankAccount)):
            raise InvalidOperationError(f"Unsupported account class: {account_class!r}")
        account = account_class(client.as_owner(), **kwargs)
        if account.account_id in self._accounts:
            raise InvalidOperationError(f"Account {account.account_id} already exists")
        self._accounts[account.account_id] = account
        self._account_owners[account.account_id] = client_id
        client.link_account(account.account_id)
        return account

    def get_account(self, account_id: str) -> BankAccount:
        account = self._accounts.get(account_id)
        if account is None:
            raise AccountNotFoundError(account_id)
        return account

    def close_account(self, account_id: str) -> None:
        self._get_operable_account(account_id, "close the account").close()

    def freeze_account(self, account_id: str) -> None:
        # Freezing protects the client, so it is allowed at night and for blocked clients
        self.get_account(account_id).freeze()

    def unfreeze_account(self, account_id: str) -> None:
        self._get_operable_account(account_id, "unfreeze the account").unfreeze()

    def deposit(self, account_id: str, amount: Amount) -> Decimal:
        account = self._get_operable_account(account_id, "deposit")
        self._flag_if_large("deposit", account, amount)
        return account.deposit(amount)

    def withdraw(self, account_id: str, amount: Amount) -> Decimal:
        account = self._get_operable_account(account_id, "withdraw")
        self._flag_if_large("withdrawal", account, amount)
        return account.withdraw(amount)

    def search_accounts(
        self,
        *,
        client_id: Optional[str] = None,
        owner_name: Optional[str] = None,
        status: Optional[Union[AccountStatus, str]] = None,
        currency: Optional[Union[Currency, str]] = None,
        account_type: Optional[type[BankAccount]] = None,
        min_balance: Optional[Amount] = None,
        max_balance: Optional[Amount] = None,
    ) -> list[BankAccount]:
        """Return the accounts matching every given filter, in opening order.

        ``owner_name`` is a case-insensitive substring; ``account_type`` also
        matches subclasses.
        """
        if client_id is not None:
            self.get_client(client_id)
        if status is not None:
            try:
                status = AccountStatus(status)
            except ValueError:
                raise InvalidOperationError(f"Unknown account status: {status!r}") from None
        if currency is not None:
            currency = Currency.parse(currency)
        name_part = owner_name.casefold() if owner_name is not None else None
        low = to_money(min_balance, allow_zero=True) if min_balance is not None else None
        high = to_money(max_balance, allow_zero=True) if max_balance is not None else None

        result = []
        for account_id, account in self._accounts.items():
            if client_id is not None and self._account_owners[account_id] != client_id:
                continue
            if name_part is not None and name_part not in account.owner.full_name.casefold():
                continue
            if status is not None and account.status is not status:
                continue
            if currency is not None and account.currency is not currency:
                continue
            if account_type is not None and not isinstance(account, account_type):
                continue
            if low is not None and account.balance < low:
                continue
            if high is not None and account.balance > high:
                continue
            result.append(account)
        return result

    # --- Reports ---

    def get_total_balance(self, currency: Union[Currency, str] = Currency.USD) -> Decimal:
        """Total money in open accounts of one currency (invested money included).

        There are no exchange rates in the model, so currencies are never mixed.
        """
        return sum((self._account_value(a) for a in self._open_accounts(currency)), Decimal("0.00"))

    def get_clients_ranking(
        self, currency: Union[Currency, str] = Currency.USD
    ) -> list[ClientRank]:
        """Clients with open accounts in the currency, richest first (ties by name)."""
        totals: dict[str, Decimal] = {}
        for account in self._open_accounts(currency):
            client_id = self._account_owners[account.account_id]
            totals[client_id] = totals.get(client_id, Decimal("0.00")) + self._account_value(
                account
            )
        ordered = sorted(
            totals.items(), key=lambda item: (-item[1], self._clients[item[0]].full_name)
        )
        return [
            ClientRank(position, self._clients[client_id], total)
            for position, (client_id, total) in enumerate(ordered, start=1)
        ]

    def get_suspicious_activities(
        self, client_id: Optional[str] = None
    ) -> list[SuspiciousActivity]:
        return [a for a in self._suspicious if client_id is None or a.client_id == client_id]

    # --- Internal checks ---

    def _open_accounts(self, currency: Union[Currency, str]) -> list[BankAccount]:
        return [
            a
            for a in self.search_accounts(currency=currency)
            if a.status is not AccountStatus.CLOSED
        ]

    @staticmethod
    def _account_value(account: BankAccount) -> Decimal:
        # Invested money still belongs to the client, so it counts towards their total
        if isinstance(account, InvestmentAccount):
            return account.total_value
        return account.balance

    def _flag(
        self, reason: str, *, client_id: Optional[str] = None, account_id: Optional[str] = None
    ) -> None:
        self._suspicious.append(SuspiciousActivity(self._clock(), reason, client_id, account_id))

    def _flag_if_large(self, operation: str, account: BankAccount, amount: Amount) -> None:
        amount = to_money(amount)
        if amount >= self.LARGE_OPERATION_THRESHOLD:
            self._flag(
                f"Large {operation}: {format_money(amount)} {account.currency.value}",
                client_id=self._account_owners[account.account_id],
                account_id=account.account_id,
            )

    def _is_night(self) -> bool:
        return self.NIGHT_START <= self._clock().time() < self.NIGHT_END

    def _ensure_working_hours(
        self, action: str, *, client_id: Optional[str] = None, account_id: Optional[str] = None
    ) -> None:
        if self._is_night():
            self._flag(f"Night attempt to {action}", client_id=client_id, account_id=account_id)
            raise OperationTimeError(
                f"Cannot {action}: operations are not allowed from "
                f"{self.NIGHT_START:%H:%M} to {self.NIGHT_END:%H:%M}"
            )

    def _get_active_client(self, client_id: str) -> Client:
        client = self.get_client(client_id)
        if client.is_blocked:
            raise ClientBlockedError(client_id)
        return client

    def _get_operable_account(self, account_id: str, action: str) -> BankAccount:
        """The account, if the time of day and its owner's status allow the action."""
        account = self.get_account(account_id)
        client_id = self._account_owners[account_id]
        self._ensure_working_hours(action, client_id=client_id, account_id=account_id)
        self._get_active_client(client_id)
        return account

    def __str__(self) -> str:
        return (
            f"Bank {self._name} | Clients: {len(self._clients)} | Accounts: {len(self._accounts)}"
        )
