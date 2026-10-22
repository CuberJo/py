"""Transactions: the transaction model, a priority queue and the processor."""

import itertools
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Mapping, Optional, Union

from accounts import PremiumAccount
from audit import AuditLevel, AuditLog
from bank import Bank
from exceptions import (
    AccountClosedError,
    AccountFrozenError,
    AccountNotFoundError,
    BankError,
    InsufficientFundsError,
    InvalidOperationError,
    OperationTimeError,
    SuspiciousOperationError,
    TransactionNotFoundError,
)
from models import AccountStatus, BankAccount, Currency
from utils import (
    Amount,
    Rate,
    format_money,
    generate_short_id,
    mask_account_id,
    round_money,
    to_money,
    to_rate,
)

if TYPE_CHECKING:
    from risk import RiskAnalyzer, RiskAssessment


class TransactionType(Enum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    TRANSFER = "transfer"  # between two accounts of the bank
    EXTERNAL_TRANSFER = "external_transfer"  # to an account in another bank

    @classmethod
    def parse(cls, value: Union["TransactionType", str]) -> "TransactionType":
        """Accept a TransactionType member or a case-insensitive name such as 'Transfer'."""
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).lower())
        except ValueError:
            allowed = ", ".join(t.value for t in cls)
            raise InvalidOperationError(
                f"Unsupported transaction type: {value!r}. Allowed: {allowed}"
            ) from None


class TransactionStatus(Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Transaction:
    """One money operation and its outcome.

    A transaction starts as PENDING. The processor fills in the fee and the
    debited and credited amounts and moves it to COMPLETED, or to FAILED with
    the reason. A pending transaction can be cancelled. COMPLETED, FAILED and
    CANCELLED are final.
    """

    # Which types take money from a sender and which give it to a receiver
    _SENDER_TYPES = frozenset(
        {TransactionType.WITHDRAWAL, TransactionType.TRANSFER, TransactionType.EXTERNAL_TRANSFER}
    )
    _RECEIVER_TYPES = frozenset(
        {TransactionType.DEPOSIT, TransactionType.TRANSFER, TransactionType.EXTERNAL_TRANSFER}
    )

    def __init__(
        self,
        transaction_type: Union[TransactionType, str],
        amount: Amount,
        currency: Union[Currency, str] = Currency.USD,
        *,
        sender_id: Optional[str] = None,
        receiver_id: Optional[str] = None,
        priority: int = 0,
        scheduled_at: Optional[datetime] = None,
        transaction_id: Optional[str] = None,
        created_at: Optional[datetime] = None,
        id_generator: Callable[[], str] = generate_short_id,
    ):
        # ``created_at`` and the ID generator are injectable so tests get fixed values
        self._type = TransactionType.parse(transaction_type)
        self._amount = to_money(amount)
        self._currency = Currency.parse(currency)
        self._sender_id = self._check_party("sender", sender_id, self._SENDER_TYPES)
        self._receiver_id = self._check_party("receiver", receiver_id, self._RECEIVER_TYPES)
        if self._sender_id is not None and self._sender_id == self._receiver_id:
            raise InvalidOperationError("Sender and receiver must be different accounts")
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise InvalidOperationError(f"Priority must be an integer, got: {priority!r}")
        for name, value in (("scheduled_at", scheduled_at), ("created_at", created_at)):
            if value is not None and not isinstance(value, datetime):
                raise InvalidOperationError(f"{name} must be a datetime, got: {value!r}")
        if transaction_id is None:
            transaction_id = id_generator()
        if not isinstance(transaction_id, str) or not transaction_id.strip():
            raise InvalidOperationError("Transaction ID must be a non-empty string")

        self._transaction_id = transaction_id.strip()
        self._priority = priority
        self._created_at = created_at or datetime.now()
        self._scheduled_at = scheduled_at
        self._processed_at: Optional[datetime] = None
        self._status = TransactionStatus.PENDING
        self._failure_reason: Optional[str] = None
        self._attempts = 0
        self._fee = Decimal("0.00")
        self._debited_amount: Optional[Decimal] = None
        self._credited_amount: Optional[Decimal] = None

    def _check_party(
        self, role: str, account_id: Optional[str], types: frozenset[TransactionType]
    ) -> Optional[str]:
        if self._type not in types:
            if account_id is not None:
                raise InvalidOperationError(f"A {self._type.value} has no {role} account")
            return None
        if not isinstance(account_id, str) or not account_id.strip():
            raise InvalidOperationError(f"A {self._type.value} needs a {role} account")
        return account_id.strip()

    @property
    def transaction_id(self) -> str:
        return self._transaction_id

    @property
    def transaction_type(self) -> TransactionType:
        return self._type

    @property
    def amount(self) -> Decimal:
        return self._amount

    @property
    def currency(self) -> Currency:
        return self._currency

    @property
    def fee(self) -> Decimal:
        """Fee charged in the transaction currency; zero until it completes."""
        return self._fee

    @property
    def sender_id(self) -> Optional[str]:
        return self._sender_id

    @property
    def receiver_id(self) -> Optional[str]:
        return self._receiver_id

    @property
    def status(self) -> TransactionStatus:
        return self._status

    @property
    def failure_reason(self) -> Optional[str]:
        return self._failure_reason

    @property
    def priority(self) -> int:
        return self._priority

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def scheduled_at(self) -> Optional[datetime]:
        return self._scheduled_at

    @property
    def processed_at(self) -> Optional[datetime]:
        return self._processed_at

    @property
    def attempts(self) -> int:
        return self._attempts

    @property
    def debited_amount(self) -> Optional[Decimal]:
        """Amount plus fee taken from the sender, in the sender's currency."""
        return self._debited_amount

    @property
    def credited_amount(self) -> Optional[Decimal]:
        """Amount given to the receiver, in the receiver's currency."""
        return self._credited_amount

    def is_due(self, now: datetime) -> bool:
        """Pending and not scheduled for later than ``now``."""
        return self._status is TransactionStatus.PENDING and (
            self._scheduled_at is None or self._scheduled_at <= now
        )

    def cancel(self, when: Optional[datetime] = None, reason: str = "Cancelled") -> None:
        self._finish(TransactionStatus.CANCELLED, when or datetime.now(), reason)

    # --- Used by the processor ---

    def _start_attempt(self) -> None:
        self._ensure_pending()
        self._attempts += 1

    def _reschedule(self, when: datetime) -> None:
        self._ensure_pending()
        self._scheduled_at = when

    def _complete(
        self,
        when: datetime,
        fee: Decimal,
        debited: Optional[Decimal],
        credited: Optional[Decimal],
    ) -> None:
        self._finish(TransactionStatus.COMPLETED, when)
        self._fee = fee
        self._debited_amount = debited
        self._credited_amount = credited

    def _fail(self, when: datetime, reason: str) -> None:
        self._finish(TransactionStatus.FAILED, when, reason)

    def _ensure_pending(self) -> None:
        if self._status is not TransactionStatus.PENDING:
            raise InvalidOperationError(
                f"Transaction {self._transaction_id} is already {self._status.value}"
            )

    def _finish(
        self, status: TransactionStatus, when: datetime, reason: Optional[str] = None
    ) -> None:
        self._ensure_pending()
        self._status = status
        self._processed_at = when
        self._failure_reason = reason

    def get_transaction_info(self) -> dict[str, Any]:
        return {
            "transaction_id": self._transaction_id,
            "type": self._type.value,
            "amount": self._amount,
            "currency": self._currency.value,
            "fee": self._fee,
            "sender_id": self._sender_id,
            "receiver_id": self._receiver_id,
            "status": self._status.value,
            "failure_reason": self._failure_reason,
            "priority": self._priority,
            "attempts": self._attempts,
            "debited_amount": self._debited_amount,
            "credited_amount": self._credited_amount,
            "created_at": self._created_at,
            "scheduled_at": self._scheduled_at,
            "processed_at": self._processed_at,
        }

    def __str__(self) -> str:
        sender = mask_account_id(self._sender_id) if self._sender_id else "-"
        receiver = mask_account_id(self._receiver_id) if self._receiver_id else "-"
        text = (
            f"{self._type.value} {self._transaction_id} | "
            f"{format_money(self._amount)} {self._currency.value} | "
            f"{sender} -> {receiver} | Fee: {format_money(self._fee)} | "
            f"Status: {self._status.value}"
        )
        if self._failure_reason:
            text += f" ({self._failure_reason})"
        return text

    def __repr__(self) -> str:
        return (
            f"Transaction(transaction_id={self._transaction_id!r}, type={self._type}, "
            f"amount={self._amount!r}, currency={self._currency}, status={self._status})"
        )


class TransactionQueue:
    """Pending transactions in processing order: higher priority first, then the order added.

    A transaction with ``scheduled_at`` in the future stays in the queue until
    that time.
    """

    def __init__(self, *, clock: Callable[[], datetime] = datetime.now):
        # The clock is injected so tests and the demo can control the time
        self._clock = clock
        self._entries: dict[str, tuple[int, Transaction]] = {}  # id -> (sequence, transaction)
        self._sequence = itertools.count()

    def add(self, transaction: Transaction) -> Transaction:
        if not isinstance(transaction, Transaction):
            raise InvalidOperationError("transaction must be a Transaction instance")
        if transaction.status is not TransactionStatus.PENDING:
            raise InvalidOperationError(
                f"Only pending transactions can be queued, "
                f"{transaction.transaction_id} is {transaction.status.value}"
            )
        if transaction.transaction_id in self._entries:
            raise InvalidOperationError(
                f"Transaction {transaction.transaction_id} is already in the queue"
            )
        self._entries[transaction.transaction_id] = (next(self._sequence), transaction)
        return transaction

    def cancel(self, transaction_id: str, reason: str = "Cancelled") -> Transaction:
        """Remove a queued transaction and mark it cancelled."""
        entry = self._entries.pop(transaction_id, None)
        if entry is None:
            raise TransactionNotFoundError(transaction_id)
        transaction = entry[1]
        transaction.cancel(self._clock(), reason)
        return transaction

    def pending(self) -> list[Transaction]:
        """Every queued transaction in processing order, deferred ones included."""
        return [t for _, t in sorted(self._entries.values(), key=self._order)]

    def ready(self, now: Optional[datetime] = None) -> list[Transaction]:
        """Queued transactions that are due at ``now``, in processing order."""
        if now is None:
            now = self._clock()
        return [t for t in self.pending() if t.is_due(now)]

    def pop_next(self, now: Optional[datetime] = None) -> Optional[Transaction]:
        """Take the first due transaction out of the queue, or return None."""
        ready = self.ready(now)
        if not ready:
            return None
        del self._entries[ready[0].transaction_id]
        return ready[0]

    @staticmethod
    def _order(entry: tuple[int, Transaction]) -> tuple[int, int]:
        sequence, transaction = entry
        return -transaction.priority, sequence

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, transaction_id: object) -> bool:
        return transaction_id in self._entries


class ExchangeRates:
    """Fixed exchange rates: what one unit of each currency is worth in the base currency.

    ``ExchangeRates({"EUR": 1.08})`` with base USD means 1 EUR = 1.08 USD.
    The rates are set by the caller; the model has no market data.
    """

    def __init__(
        self,
        rates: Mapping[Union[Currency, str], Rate],
        base: Union[Currency, str] = Currency.USD,
    ):
        self._base = Currency.parse(base)
        self._rates: dict[Currency, Decimal] = {self._base: Decimal(1)}
        for key, value in rates.items():
            currency = Currency.parse(key)
            rate = to_rate(value)
            if rate == 0:
                raise InvalidOperationError(f"Exchange rate for {currency.value} must be positive")
            if currency is self._base and rate != 1:
                raise InvalidOperationError(f"The base currency {currency.value} must have rate 1")
            self._rates[currency] = rate

    @property
    def base(self) -> Currency:
        return self._base

    def convert(
        self, amount: Amount, source: Union[Currency, str], target: Union[Currency, str]
    ) -> Decimal:
        """Convert an amount and round it to cents."""
        amount = to_money(amount)
        source, target = Currency.parse(source), Currency.parse(target)
        if source is target:
            return amount
        for currency in (source, target):
            if currency not in self._rates:
                raise InvalidOperationError(f"No exchange rate for {currency.value}")
        return round_money(amount * self._rates[source] / self._rates[target])


def find_client_id(bank: Bank, transaction: Transaction) -> Optional[str]:
    """The client a transaction belongs to: the sender's owner, or the receiver's for a deposit.

    Raises ``AccountNotFoundError`` if that account is not in the bank.
    """
    account_id = transaction.sender_id or transaction.receiver_id
    return bank.get_account_owner_id(account_id) if account_id is not None else None


@dataclass(frozen=True)
class ProcessingError:
    """One failed attempt to process a transaction."""

    timestamp: datetime
    transaction_id: str
    attempt: int
    error: str  # exception class name
    message: str
    will_retry: bool


class TransactionProcessor:
    """Executes transactions on a bank: fees, currency conversion, retries and an error log.

    Rules, checked before any money moves:
    - only a premium account may go below zero (within its overdraft);
    - a frozen or closed account can neither send nor receive money;
    - an external transfer costs ``external_fee_rate`` of the amount.

    An error from ``RETRYABLE_ERRORS`` (a night-time rejection) is retried after
    ``retry_delay``, up to ``max_attempts`` attempts in total; any other error
    fails the transaction at once. Every failed attempt goes to the error log.

    With a ``risk_analyzer`` every attempt is scored first, and a transaction
    the analyzer marks as blocked fails with ``SuspiciousOperationError``
    without moving money. With an ``audit_log`` every outcome is recorded there.
    """

    RETRYABLE_ERRORS: tuple[type[BankError], ...] = (OperationTimeError,)
    DEFAULT_EXTERNAL_FEE_RATE = Decimal("0.01")
    DEFAULT_MAX_ATTEMPTS = 3
    DEFAULT_RETRY_DELAY = timedelta(minutes=30)

    def __init__(
        self,
        bank: Bank,
        *,
        rates: Optional[ExchangeRates] = None,
        external_fee_rate: Rate = DEFAULT_EXTERNAL_FEE_RATE,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_delay: timedelta = DEFAULT_RETRY_DELAY,
        audit_log: Optional[AuditLog] = None,
        risk_analyzer: Optional["RiskAnalyzer"] = None,
    ):
        # Imported here: risk.py imports this module, so a top-level import would be circular
        from risk import RiskAnalyzer

        if not isinstance(bank, Bank):
            raise InvalidOperationError("bank must be a Bank instance")
        if rates is not None and not isinstance(rates, ExchangeRates):
            raise InvalidOperationError("rates must be an ExchangeRates instance")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
            raise InvalidOperationError(f"max_attempts must be at least 1, got: {max_attempts!r}")
        if not isinstance(retry_delay, timedelta) or retry_delay <= timedelta(0):
            raise InvalidOperationError(f"retry_delay must be positive, got: {retry_delay!r}")
        if audit_log is not None and not isinstance(audit_log, AuditLog):
            raise InvalidOperationError("audit_log must be an AuditLog instance")
        if risk_analyzer is not None and not isinstance(risk_analyzer, RiskAnalyzer):
            raise InvalidOperationError("risk_analyzer must be a RiskAnalyzer instance")
        self._bank = bank
        self._rates = rates
        self._external_fee_rate = to_rate(external_fee_rate, maximum=Decimal(1))
        self._max_attempts = max_attempts
        self._retry_delay = retry_delay
        self._errors: list[ProcessingError] = []
        self._audit_log = audit_log
        self._risk_analyzer = risk_analyzer

    @property
    def error_log(self) -> list[ProcessingError]:
        return list(self._errors)

    def calculate_fee(self, transaction: Transaction) -> Decimal:
        """Fee in the transaction currency: only external transfers are charged."""
        if transaction.transaction_type is TransactionType.EXTERNAL_TRANSFER:
            return round_money(transaction.amount * self._external_fee_rate)
        return Decimal("0.00")

    def process(self, transaction: Transaction) -> Transaction:
        """Make one attempt to execute the transaction.

        Afterwards it is COMPLETED, FAILED, or still PENDING with a later
        ``scheduled_at`` if the error can be retried.
        """
        if not isinstance(transaction, Transaction):
            raise InvalidOperationError("transaction must be a Transaction instance")
        transaction._start_attempt()
        now = self._bank.now()
        fee = self.calculate_fee(transaction)
        try:
            self._check_risk(transaction, now)
            debited, credited = self._execute(transaction, fee)
        except BankError as error:
            will_retry = (
                isinstance(error, self.RETRYABLE_ERRORS)
                and transaction.attempts < self._max_attempts
            )
            self._errors.append(
                ProcessingError(
                    now,
                    transaction.transaction_id,
                    transaction.attempts,
                    type(error).__name__,
                    str(error),
                    will_retry,
                )
            )
            if will_retry:
                transaction._reschedule(now + self._retry_delay)
            else:
                transaction._fail(now, f"{type(error).__name__}: {error}")
            self._audit_failure(transaction, error, will_retry, now)
        else:
            transaction._complete(now, fee, debited, credited)
            self._audit(
                AuditLevel.INFO,
                "transaction_completed",
                str(transaction),
                transaction,
                now,
                amount=transaction.amount,
                currency=transaction.currency.value,
                fee=fee,
            )
        return transaction

    def run(self, queue: TransactionQueue) -> list[Transaction]:
        """Process every transaction that is due now, in queue order.

        Transactions to retry go back to the queue with a later time.
        Returns the transactions attempted in this run.
        """
        now = self._bank.now()
        attempted = []
        while (transaction := queue.pop_next(now)) is not None:
            self.process(transaction)
            if transaction.status is TransactionStatus.PENDING:
                queue.add(transaction)
            attempted.append(transaction)
        return attempted

    def _check_risk(self, transaction: Transaction, now: datetime) -> None:
        """Score the attempt; raise ``SuspiciousOperationError`` if it must be blocked."""
        if self._risk_analyzer is None:
            return
        assessment = self._risk_analyzer.check(transaction, now)
        if assessment.blocked:
            raise SuspiciousOperationError(
                transaction.transaction_id, assessment.level.value, assessment.reasons
            )
        if assessment.factors:
            # Low risk with a factor is noted; medium risk is a warning
            self._audit(
                AuditLevel.INFO if assessment.level.value == "low" else AuditLevel.WARNING,
                "risk_flagged",
                f"{assessment.level.value} risk, allowed: {', '.join(assessment.reasons)}",
                transaction,
                now,
                assessment=assessment,
            )

    def _audit_failure(
        self, transaction: Transaction, error: BankError, will_retry: bool, now: datetime
    ) -> None:
        if isinstance(error, SuspiciousOperationError):
            level, event = AuditLevel.CRITICAL, "transaction_blocked"
        elif will_retry:
            level, event = AuditLevel.WARNING, "transaction_retry"
        else:
            level, event = AuditLevel.ERROR, "transaction_failed"
        self._audit(
            level,
            event,
            f"{type(error).__name__}: {error}",
            transaction,
            now,
            error=type(error).__name__,
            attempt=transaction.attempts,
        )

    def _audit(
        self,
        level: AuditLevel,
        event: str,
        message: str,
        transaction: Transaction,
        now: datetime,
        *,
        assessment: Optional["RiskAssessment"] = None,
        **details: Any,
    ) -> None:
        if self._audit_log is None:
            return
        try:
            client_id = find_client_id(self._bank, transaction)
        except AccountNotFoundError:
            client_id = None
        if assessment is not None:
            details.update(
                risk_level=assessment.level.value,
                risk_score=assessment.score,
                risk_factors=assessment.reasons,
            )
        self._audit_log.log(
            level,
            event,
            message,
            timestamp=now,
            client_id=client_id,
            account_id=transaction.sender_id or transaction.receiver_id,
            transaction_id=transaction.transaction_id,
            details=details,
        )

    def _execute(
        self, transaction: Transaction, fee: Decimal
    ) -> tuple[Optional[Decimal], Optional[Decimal]]:
        """Check the rules, convert the amounts and move the money through the bank."""
        kind = transaction.transaction_type
        sender = receiver = None
        if transaction.sender_id is not None:
            sender = self._bank.get_account(transaction.sender_id)
        if kind is TransactionType.EXTERNAL_TRANSFER:
            if self._is_bank_account(transaction.receiver_id):
                raise InvalidOperationError(
                    f"Account {transaction.receiver_id} is in this bank, "
                    f"use an internal transfer"
                )
        elif transaction.receiver_id is not None:
            receiver = self._bank.get_account(transaction.receiver_id)

        for account in (sender, receiver):
            if account is not None:
                self._ensure_can_move_money(account)

        debited = credited = None
        if sender is not None:
            debited = self._convert(transaction.amount + fee, transaction.currency, sender.currency)
            self._ensure_no_negative_balance(sender, debited)
        if receiver is not None:
            credited = self._convert(transaction.amount, transaction.currency, receiver.currency)

        if kind is TransactionType.DEPOSIT:
            self._bank.deposit(receiver.account_id, credited)
        elif kind is TransactionType.WITHDRAWAL:
            self._bank.withdraw(sender.account_id, debited)
        elif kind is TransactionType.TRANSFER:
            self._bank.transfer(sender.account_id, receiver.account_id, debited, credited)
        else:
            # The money leaves the bank; the other bank receives the amount without the fee
            self._bank.withdraw(sender.account_id, debited)
            credited = transaction.amount
        return debited, credited

    def _convert(self, amount: Decimal, source: Currency, target: Currency) -> Decimal:
        if source is target:
            return amount
        if self._rates is None:
            raise InvalidOperationError(
                f"No exchange rates set, cannot convert {source.value} to {target.value}"
            )
        return self._rates.convert(amount, source, target)

    def _is_bank_account(self, account_id: str) -> bool:
        try:
            self._bank.get_account(account_id)
        except AccountNotFoundError:
            return False
        return True

    @staticmethod
    def _ensure_can_move_money(account: BankAccount) -> None:
        if account.status is AccountStatus.FROZEN:
            raise AccountFrozenError(account.account_id)
        if account.status is AccountStatus.CLOSED:
            raise AccountClosedError(account.account_id)

    @staticmethod
    def _ensure_no_negative_balance(account: BankAccount, debit: Decimal) -> None:
        # Checked here so the rule holds for any account type, not only for the
        # types whose withdraw() already refuses to go below zero
        if not isinstance(account, PremiumAccount) and debit > account.balance:
            raise InsufficientFundsError(account.balance, debit, account.currency.value)
