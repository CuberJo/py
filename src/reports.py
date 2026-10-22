"""Reports: suspicious operations, client risk profiles, error and transaction statistics."""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Union

from audit import AuditLog
from bank import Bank, ClientRank
from exceptions import InvalidOperationError
from models import Currency
from risk import ClientRiskProfile, RiskAnalyzer, RiskAssessment, RiskLevel
from transactions import ExchangeRates, Transaction, TransactionStatus, TransactionType
from utils import format_money, format_percent

# Processor events that end an attempt; their count is the number of attempts
ATTEMPT_EVENTS = (
    "transaction_completed",
    "transaction_failed",
    "transaction_retry",
    "transaction_blocked",
)


@dataclass(frozen=True)
class ErrorStatistics:
    """Failed transaction attempts, counted from the audit log."""

    attempts: int
    errors: int
    retried: int  # rejected for now, will be tried again
    failed: int  # failed for good
    blocked: int  # blocked as dangerous
    by_error: dict[str, int]  # exception name -> count, most frequent first

    @property
    def error_rate(self) -> Decimal:
        """Share of attempts that ended in an error, as a fraction."""
        if not self.attempts:
            return Decimal(0)
        return Decimal(self.errors) / Decimal(self.attempts)


class AuditReport:
    """Builds reports from the audit log and the risk analyzer's assessments."""

    def __init__(self, audit_log: AuditLog, risk_analyzer: RiskAnalyzer):
        if not isinstance(audit_log, AuditLog):
            raise InvalidOperationError("audit_log must be an AuditLog instance")
        if not isinstance(risk_analyzer, RiskAnalyzer):
            raise InvalidOperationError("risk_analyzer must be a RiskAnalyzer instance")
        self._audit_log = audit_log
        self._risk_analyzer = risk_analyzer

    def suspicious_operations(
        self, min_level: Union[RiskLevel, str] = RiskLevel.MEDIUM
    ) -> list[RiskAssessment]:
        """Transactions assessed at ``min_level`` or above, highest score first."""
        found = self._risk_analyzer.assessments(min_level=min_level)
        # sorted() is stable, so equal scores keep the order they were checked in
        return sorted(found, key=lambda a: -a.score)

    def client_risk_profile(self, client_id: str) -> ClientRiskProfile:
        return self._risk_analyzer.get_client_profile(client_id)

    def client_risk_profiles(self) -> list[ClientRiskProfile]:
        """Profiles of every client with assessed transactions, riskiest first."""
        client_ids = dict.fromkeys(
            a.client_id for a in self._risk_analyzer.assessments() if a.client_id is not None
        )
        profiles = [self.client_risk_profile(client_id) for client_id in client_ids]
        return sorted(profiles, key=lambda p: (-p.level.severity, -p.max_score, p.client_id))

    def error_statistics(self) -> ErrorStatistics:
        attempts = errors = retried = failed = blocked = 0
        by_error: Counter[str] = Counter()
        for entry in self._audit_log.entries:
            if entry.event not in ATTEMPT_EVENTS:
                continue
            attempts += 1
            if entry.event == "transaction_completed":
                continue
            errors += 1
            by_error[entry.details.get("error", "unknown")] += 1
            if entry.event == "transaction_retry":
                retried += 1
            elif entry.event == "transaction_blocked":
                blocked += 1
            else:
                failed += 1
        return ErrorStatistics(
            attempts, errors, retried, failed, blocked, dict(by_error.most_common())
        )

    def render(self, client_id: Optional[str] = None) -> str:
        """The three reports as text; ``client_id`` limits the profiles to one client."""
        lines = ["Suspicious operations:"]
        suspicious = self.suspicious_operations()
        lines += [f"  {a}" for a in suspicious] or ["  none"]

        lines.append("Client risk profiles:")
        profiles = (
            [self.client_risk_profile(client_id)]
            if client_id is not None
            else self.client_risk_profiles()
        )
        for profile in profiles:
            levels = ", ".join(f"{lvl.value} {n}" for lvl, n in profile.by_level.items())
            factors = ", ".join(f"{f.value} x{n}" for f, n in profile.factors.items()) or "none"
            lines.append(
                f"  {profile.client_id}: {profile.level.value} risk | "
                f"operations {profile.operations} ({levels}) | blocked {profile.blocked} | "
                f"max score {profile.max_score} | factors: {factors}"
            )
        if not profiles:
            lines.append("  none")

        stats = self.error_statistics()
        lines.append("Error statistics:")
        lines.append(
            f"  attempts {stats.attempts}, errors {stats.errors} "
            f"({format_percent(stats.error_rate)}): failed {stats.failed}, "
            f"blocked {stats.blocked}, retried {stats.retried}"
        )
        lines += [f"  {name}: {count}" for name, count in stats.by_error.items()]
        return "\n".join(lines)


@dataclass(frozen=True)
class TransactionStatistics:
    """Counts and totals over a set of transactions; money is in the base currency."""

    total: int
    by_status: dict[TransactionStatus, int]
    by_type: dict[TransactionType, int]
    volume: Decimal  # amounts of the completed transactions
    fees: Decimal  # fees of the completed transactions

    @property
    def success_rate(self) -> Decimal:
        """Share of finished transactions that completed, as a fraction; cancelled ones excluded."""
        completed = self.by_status[TransactionStatus.COMPLETED]
        finished = completed + self.by_status[TransactionStatus.FAILED]
        if not finished:
            return Decimal(0)
        return Decimal(completed) / Decimal(finished)


class TransactionReport:
    """Bank-wide reports over the processed transactions, with every currency in ``rates.base``.

    ``Bank`` never mixes currencies; this report converts them with the given
    exchange rates, so it can rank clients and add up balances across currencies.
    """

    def __init__(self, bank: Bank, transactions: Iterable[Transaction], rates: ExchangeRates):
        if not isinstance(bank, Bank):
            raise InvalidOperationError("bank must be a Bank instance")
        if not isinstance(rates, ExchangeRates):
            raise InvalidOperationError("rates must be an ExchangeRates instance")
        self._bank = bank
        self._transactions = list(transactions)
        if not all(isinstance(t, Transaction) for t in self._transactions):
            raise InvalidOperationError("transactions must be Transaction instances")
        self._rates = rates

    @property
    def base_currency(self) -> Currency:
        return self._rates.base

    def client_history(self, client_id: str) -> list[Transaction]:
        """Transactions that touch any of the client's accounts, oldest first."""
        account_ids = set(self._bank.get_client(client_id).account_ids)
        found = [
            t
            for t in self._transactions
            if t.sender_id in account_ids or t.receiver_id in account_ids
        ]
        return sorted(found, key=lambda t: t.created_at)

    def statistics(self) -> TransactionStatistics:
        by_status = {status: 0 for status in TransactionStatus}
        by_type = {kind: 0 for kind in TransactionType}
        volume = fees = Decimal("0.00")
        for transaction in self._transactions:
            by_status[transaction.status] += 1
            by_type[transaction.transaction_type] += 1
            if transaction.status is TransactionStatus.COMPLETED:
                volume += self._to_base(transaction.amount, transaction.currency)
                fees += self._to_base(transaction.fee, transaction.currency)
        return TransactionStatistics(len(self._transactions), by_status, by_type, volume, fees)

    def total_balance(self) -> Decimal:
        """Money in all open accounts of the bank."""
        return sum(
            (self._to_base(self._bank.get_total_balance(c), c) for c in Currency),
            Decimal("0.00"),
        )

    def top_clients(self, limit: int = 3) -> list[ClientRank]:
        """The richest clients over all their accounts (ties by name)."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise InvalidOperationError(f"limit must be at least 1, got: {limit!r}")
        totals: dict[str, Decimal] = {}
        clients = {}
        for currency in Currency:
            for rank in self._bank.get_clients_ranking(currency):
                client_id = rank.client.client_id
                clients[client_id] = rank.client
                totals[client_id] = totals.get(client_id, Decimal("0.00")) + self._to_base(
                    rank.total, currency
                )
        ordered = sorted(totals.items(), key=lambda item: (-item[1], clients[item[0]].full_name))
        return [
            ClientRank(position, clients[client_id], total)
            for position, (client_id, total) in enumerate(ordered[:limit], start=1)
        ]

    def render(self, top: int = 3) -> str:
        base = self.base_currency.value
        lines = [f"Top-{top} clients ({base}, all currencies):"]
        lines += [
            f"  {r.position}. {r.client.full_name}: {format_money(r.total)}"
            for r in self.top_clients(top)
        ] or ["  none"]

        stats = self.statistics()
        lines.append("Transaction statistics:")
        statuses = ", ".join(f"{s.value} {n}" for s, n in stats.by_status.items())
        types = ", ".join(f"{t.value} {n}" for t, n in stats.by_type.items())
        lines.append(f"  total {stats.total}: {statuses}")
        lines.append(f"  by type: {types}")
        lines.append(
            f"  success rate {format_percent(stats.success_rate)}, "
            f"volume {format_money(stats.volume)} {base}, fees {format_money(stats.fees)} {base}"
        )

        lines.append("Total balance:")
        for currency in Currency:
            amount = self._bank.get_total_balance(currency)
            if amount:
                lines.append(f"  {currency.value}: {format_money(amount)}")
        lines.append(f"  all accounts in {base}: {format_money(self.total_balance())}")
        return "\n".join(lines)

    def _to_base(self, amount: Decimal, currency: Currency) -> Decimal:
        # ExchangeRates.convert() takes only positive amounts, but a premium
        # account in overdraft can make a client's total negative
        if not amount:
            return Decimal("0.00")
        converted = self._rates.convert(abs(amount), currency, self._rates.base)
        return converted if amount > 0 else -converted
