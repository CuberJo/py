"""Risk analysis: scoring transactions for signs of fraud and building client risk profiles."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Optional, Union

from bank import Bank
from exceptions import InvalidOperationError
from models import Currency
from transactions import (
    ExchangeRates,
    Transaction,
    TransactionStatus,
    TransactionType,
    find_client_id,
)
from utils import Amount, format_money, to_money


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def severity(self) -> int:
        """Position in the order LOW < MEDIUM < HIGH."""
        return list(RiskLevel).index(self)

    @classmethod
    def parse(cls, value: Union["RiskLevel", str]) -> "RiskLevel":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).lower())
        except ValueError:
            allowed = ", ".join(level.value for level in cls)
            raise InvalidOperationError(
                f"Unknown risk level: {value!r}. Allowed: {allowed}"
            ) from None


class RiskFactor(Enum):
    LARGE_AMOUNT = "large amount"
    VERY_LARGE_AMOUNT = "very large amount"
    FREQUENT_OPERATIONS = "frequent operations"
    NEW_RECEIVER = "transfer to a new receiver"
    NIGHT_TIME = "night-time operation"


@dataclass(frozen=True)
class RiskAssessment:
    """The risk of one transaction attempt: the factors found, their total score and the level."""

    transaction_id: str
    client_id: Optional[str]
    timestamp: datetime  # when the assessment was made
    amount: Decimal  # in the analyzer's base currency
    score: int
    level: RiskLevel
    factors: tuple[RiskFactor, ...]
    blocked: bool

    @property
    def reasons(self) -> list[str]:
        return [factor.value for factor in self.factors]

    def __str__(self) -> str:
        reasons = ", ".join(self.reasons) or "no risk factors"
        action = "BLOCKED" if self.blocked else "allowed"
        return (
            f"{self.transaction_id} | client {self.client_id or '-'} | "
            f"{self.level.value} risk (score {self.score}) | {action} | {reasons}"
        )


@dataclass(frozen=True)
class ClientRiskProfile:
    """Summary of a client's assessed transactions."""

    client_id: str
    operations: int
    by_level: dict[RiskLevel, int]
    blocked: int
    factors: dict[RiskFactor, int]
    max_score: int
    level: RiskLevel  # the highest level among the client's transactions


class RiskAnalyzer:
    """Scores transactions by risk factors; ``block_level`` and above are blocked.

    Factors and their weights (``WEIGHTS``):
    - large amount: at least ``large_amount`` (very large: ``very_large_amount``),
      in the base currency of ``rates``;
    - frequent operations: the client already made ``frequency_limit`` or more
      operations within ``frequency_window`` before this one;
    - new receiver: a transfer to another client's or an external account the
      client has never sent money to successfully;
    - night time: made from ``NIGHT_START`` to ``NIGHT_END``.

    Frequency and night time use ``created_at``, when the client made the
    operation, not when the queue processed it. The total score gives the
    level: below ``MEDIUM_SCORE`` low, below ``HIGH_SCORE`` medium, else high.
    """

    WEIGHTS = {
        RiskFactor.LARGE_AMOUNT: 2,
        RiskFactor.VERY_LARGE_AMOUNT: 4,
        RiskFactor.FREQUENT_OPERATIONS: 2,
        RiskFactor.NEW_RECEIVER: 1,
        RiskFactor.NIGHT_TIME: 1,
    }
    MEDIUM_SCORE = 2
    HIGH_SCORE = 4
    # Wider than the bank's closed hours, so late-evening and early-morning operations count too
    NIGHT_START = time(23, 0)
    NIGHT_END = time(6, 0)
    DEFAULT_LARGE_AMOUNT = Decimal("10000.00")
    DEFAULT_VERY_LARGE_AMOUNT = Decimal("100000.00")
    DEFAULT_FREQUENCY_LIMIT = 3
    DEFAULT_FREQUENCY_WINDOW = timedelta(minutes=10)

    def __init__(
        self,
        bank: Bank,
        *,
        rates: Optional[ExchangeRates] = None,
        large_amount: Amount = DEFAULT_LARGE_AMOUNT,
        very_large_amount: Amount = DEFAULT_VERY_LARGE_AMOUNT,
        frequency_limit: int = DEFAULT_FREQUENCY_LIMIT,
        frequency_window: timedelta = DEFAULT_FREQUENCY_WINDOW,
        block_level: Union[RiskLevel, str] = RiskLevel.HIGH,
    ):
        if not isinstance(bank, Bank):
            raise InvalidOperationError("bank must be a Bank instance")
        if rates is not None and not isinstance(rates, ExchangeRates):
            raise InvalidOperationError("rates must be an ExchangeRates instance")
        large, very_large = to_money(large_amount), to_money(very_large_amount)
        if very_large <= large:
            raise InvalidOperationError("very_large_amount must be above large_amount")
        if (
            isinstance(frequency_limit, bool)
            or not isinstance(frequency_limit, int)
            or frequency_limit < 1
        ):
            raise InvalidOperationError(
                f"frequency_limit must be at least 1, got: {frequency_limit!r}"
            )
        if not isinstance(frequency_window, timedelta) or frequency_window <= timedelta(0):
            raise InvalidOperationError(
                f"frequency_window must be positive, got: {frequency_window!r}"
            )
        self._bank = bank
        self._rates = rates
        self._base = rates.base if rates is not None else Currency.USD
        self._large_amount = large
        self._very_large_amount = very_large
        self._frequency_limit = frequency_limit
        self._frequency_window = frequency_window
        self._block_level = RiskLevel.parse(block_level)
        # Every checked attempt; a retried transaction appears once per attempt
        self._history: list[tuple[Transaction, RiskAssessment]] = []

    @property
    def base_currency(self) -> Currency:
        return self._base

    def assess(self, transaction: Transaction, now: Optional[datetime] = None) -> RiskAssessment:
        """Score the transaction without recording it."""
        if not isinstance(transaction, Transaction):
            raise InvalidOperationError("transaction must be a Transaction instance")
        client_id = find_client_id(self._bank, transaction)
        amount = self._to_base(transaction)
        factors = []
        if amount >= self._very_large_amount:
            factors.append(RiskFactor.VERY_LARGE_AMOUNT)
        elif amount >= self._large_amount:
            factors.append(RiskFactor.LARGE_AMOUNT)
        if client_id is not None and self._recent_operations(client_id, transaction) >= (
            self._frequency_limit
        ):
            factors.append(RiskFactor.FREQUENT_OPERATIONS)
        if self._is_new_receiver(transaction, client_id):
            factors.append(RiskFactor.NEW_RECEIVER)
        if self._is_night(transaction.created_at.time()):
            factors.append(RiskFactor.NIGHT_TIME)

        score = sum(self.WEIGHTS[factor] for factor in factors)
        level = self.level_for(score)
        return RiskAssessment(
            transaction_id=transaction.transaction_id,
            client_id=client_id,
            timestamp=now or self._bank.now(),
            amount=amount,
            score=score,
            level=level,
            factors=tuple(factors),
            blocked=level.severity >= self._block_level.severity,
        )

    def check(self, transaction: Transaction, now: Optional[datetime] = None) -> RiskAssessment:
        """Score the transaction and remember it for later frequency and receiver checks."""
        assessment = self.assess(transaction, now)
        self._history.append((transaction, assessment))
        return assessment

    def level_for(self, score: int) -> RiskLevel:
        if score >= self.HIGH_SCORE:
            return RiskLevel.HIGH
        if score >= self.MEDIUM_SCORE:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def assessments(
        self,
        *,
        client_id: Optional[str] = None,
        min_level: Optional[Union[RiskLevel, str]] = None,
    ) -> list[RiskAssessment]:
        """The latest assessment of every checked transaction, in the order first checked."""
        minimum = RiskLevel.parse(min_level).severity if min_level is not None else None
        latest: dict[str, RiskAssessment] = {}
        for _, assessment in self._history:
            latest[assessment.transaction_id] = assessment
        return [
            a
            for a in latest.values()
            if (client_id is None or a.client_id == client_id)
            and (minimum is None or a.level.severity >= minimum)
        ]

    def get_client_profile(self, client_id: str) -> ClientRiskProfile:
        self._bank.get_client(client_id)
        assessments = self.assessments(client_id=client_id)
        by_level = {level: 0 for level in RiskLevel}
        factors = {factor: 0 for factor in RiskFactor}
        for assessment in assessments:
            by_level[assessment.level] += 1
            for factor in assessment.factors:
                factors[factor] += 1
        level = max((a.level for a in assessments), key=lambda lvl: lvl.severity, default=None)
        return ClientRiskProfile(
            client_id=client_id,
            operations=len(assessments),
            by_level=by_level,
            blocked=sum(1 for a in assessments if a.blocked),
            factors={factor: count for factor, count in factors.items() if count},
            max_score=max((a.score for a in assessments), default=0),
            level=level or RiskLevel.LOW,
        )

    def describe_thresholds(self) -> str:
        return (
            f"large from {format_money(self._large_amount)} {self._base.value}, "
            f"very large from {format_money(self._very_large_amount)} {self._base.value}, "
            f"frequent: {self._frequency_limit}+ earlier operations in "
            f"{int(self._frequency_window.total_seconds() // 60)} min, "
            f"night {self.NIGHT_START:%H:%M}-{self.NIGHT_END:%H:%M}"
        )

    # --- Factors ---

    def _to_base(self, transaction: Transaction) -> Decimal:
        if transaction.currency is self._base:
            return transaction.amount
        if self._rates is not None:
            try:
                return self._rates.convert(transaction.amount, transaction.currency, self._base)
            except InvalidOperationError:
                pass
        # No rate: compare the raw amount, which errs on the side of flagging
        return transaction.amount

    def _recent_operations(self, client_id: str, transaction: Transaction) -> int:
        """Other transactions the client made within the window before this one."""
        start = transaction.created_at - self._frequency_window
        seen = {
            tx.transaction_id
            for tx, assessment in self._history
            if assessment.client_id == client_id
            and tx.transaction_id != transaction.transaction_id
            and start <= tx.created_at <= transaction.created_at
        }
        return len(seen)

    def _is_new_receiver(self, transaction: Transaction, client_id: Optional[str]) -> bool:
        kind = transaction.transaction_type
        if kind is TransactionType.TRANSFER:
            # Moving money between one's own accounts is not a new receiver
            if self._bank.get_account_owner_id(transaction.receiver_id) == client_id:
                return False
        elif kind is not TransactionType.EXTERNAL_TRANSFER:
            return False
        known = {
            tx.receiver_id
            for tx, assessment in self._history
            if assessment.client_id == client_id and tx.status is TransactionStatus.COMPLETED
        }
        return transaction.receiver_id not in known

    def _is_night(self, moment: time) -> bool:
        if self.NIGHT_START <= self.NIGHT_END:
            return self.NIGHT_START <= moment < self.NIGHT_END
        # The window wraps past midnight
        return moment >= self.NIGHT_START or moment < self.NIGHT_END
