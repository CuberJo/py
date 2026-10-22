import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

# Make modules from src/ importable when running pytest from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from accounts import PremiumAccount  # noqa: E402
from audit import AuditLog  # noqa: E402
from bank import Bank  # noqa: E402
from clients import Client  # noqa: E402
from exceptions import ClientNotFoundError, InvalidOperationError  # noqa: E402
from reports import TransactionReport  # noqa: E402
from transactions import (  # noqa: E402
    ExchangeRates,
    Transaction,
    TransactionProcessor,
    TransactionQueue,
    TransactionStatus,
    TransactionType,
)

DAYTIME = datetime(2026, 10, 22, 14, 0)
RATES = ExchangeRates({"EUR": "1.10", "RUB": "0.01"})


class FakeClock:
    """A clock the tests can move to any time of day."""

    def __init__(self, now: datetime = DAYTIME):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def at(hour: int, minute: int = 0, day: int = 22) -> datetime:
    return datetime(2026, 10, day, hour, minute)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def audit_log(clock: FakeClock) -> AuditLog:
    return AuditLog(clock=clock)


@pytest.fixture
def bank(clock: FakeClock) -> Bank:
    bank = Bank("Test Bank", clock=clock)
    bank.add_client(Client("John Smith", date(1985, 3, 12), "1234", client_id="CL-001"))
    bank.add_client(Client("Mary Williams", date(1992, 7, 30), "5678", client_id="CL-002"))
    bank.add_client(Client("Anna Brown", date(1990, 1, 1), "1111", client_id="CL-003"))
    bank.open_account("CL-001", account_id="J-USD", balance=1_000)
    bank.open_account("CL-001", account_id="J-EUR", balance=1_000, currency="EUR")
    bank.open_account("CL-002", PremiumAccount, account_id="M-PRM", balance=500, fee=0)
    bank.open_account("CL-002", account_id="M-RUB", balance=100_000, currency="RUB")
    bank.open_account("CL-003", account_id="A-USD", balance=1_500)
    return bank


def make_tx(transaction_type, amount, currency="USD", *, created=DAYTIME, **kwargs):
    return Transaction(transaction_type, amount, currency, created_at=created, **kwargs)


def process(bank: Bank, transactions: list[Transaction]) -> list[Transaction]:
    processor = TransactionProcessor(bank, rates=RATES)
    for tx in transactions:
        processor.process(tx)
    return transactions


# --- Queue logging ---


def test_queue_logs_queued_and_cancelled(clock, audit_log):
    queue = TransactionQueue(clock=clock, audit_log=audit_log)
    tx = queue.add(make_tx("deposit", 100, receiver_id="J-USD", transaction_id="T-1", priority=3))
    queue.cancel("T-1", "Changed my mind")
    queued, cancelled = audit_log.entries
    assert (queued.event, queued.level.value) == ("transaction_queued", "info")
    assert queued.transaction_id == "T-1"
    assert queued.account_id == "J-USD"
    assert queued.details == {"priority": 3, "attempt": 0}
    assert queued.message.startswith("Queued: deposit T-1")
    assert (cancelled.event, cancelled.message) == ("transaction_cancelled", "Changed my mind")
    assert tx.status is TransactionStatus.CANCELLED


def test_queue_logs_retry_as_queued_again(bank, clock, audit_log):
    queue = TransactionQueue(clock=clock, audit_log=audit_log)
    processor = TransactionProcessor(bank, audit_log=audit_log)
    queue.add(make_tx("deposit", 100, receiver_id="J-USD", transaction_id="T-1"))
    clock.now = at(4, 30, day=23)
    processor.run(queue)
    requeued = audit_log.filter(event="transaction_queued")[-1]
    assert requeued.details["attempt"] == 1
    assert requeued.message.startswith("Queued again for 2026-10-23 05:00")


def test_queue_without_audit_log_works(clock):
    queue = TransactionQueue(clock=clock)
    queue.add(make_tx("deposit", 100, receiver_id="J-USD", transaction_id="T-1"))
    assert len(queue) == 1


def test_queue_rejects_bad_audit_log():
    with pytest.raises(InvalidOperationError):
        TransactionQueue(audit_log="audit.jsonl")


# --- Transaction report ---


def test_client_history_covers_all_accounts_oldest_first(bank):
    transactions = process(
        bank,
        [
            make_tx("deposit", 50, receiver_id="A-USD", created=at(9), transaction_id="T-1"),
            make_tx(
                "transfer", 10, sender_id="M-PRM", receiver_id="J-EUR", created=at(11),
                transaction_id="T-2",
            ),
            make_tx("withdrawal", 20, sender_id="J-USD", created=at(10), transaction_id="T-3"),
            make_tx("deposit", 5_000, receiver_id="NOPE-0000", created=at(8), transaction_id="T-4"),
        ],
    )  # fmt: skip
    report = TransactionReport(bank, transactions, RATES)
    assert [t.transaction_id for t in report.client_history("CL-001")] == ["T-3", "T-2"]
    assert [t.transaction_id for t in report.client_history("CL-002")] == ["T-2"]
    with pytest.raises(ClientNotFoundError):
        report.client_history("CL-404")


def test_statistics(bank):
    cancelled = make_tx("deposit", 999, receiver_id="J-USD")
    cancelled.cancel(DAYTIME)
    transactions = process(
        bank,
        [
            make_tx("deposit", 100, receiver_id="J-USD"),
            make_tx("transfer", 100, "EUR", sender_id="J-EUR", receiver_id="J-USD"),
            make_tx("external_transfer", 200, sender_id="J-USD", receiver_id="DE8937040044"),
            make_tx("withdrawal", 10_000, sender_id="A-USD"),  # not enough money
        ],
    )
    stats = TransactionReport(bank, [*transactions, cancelled], RATES).statistics()
    assert stats.total == 5
    assert stats.by_status == {
        TransactionStatus.PENDING: 0,
        TransactionStatus.COMPLETED: 3,
        TransactionStatus.FAILED: 1,
        TransactionStatus.CANCELLED: 1,
    }
    assert stats.by_type[TransactionType.DEPOSIT] == 2
    assert stats.by_type[TransactionType.WITHDRAWAL] == 1
    # 100 + 100 EUR * 1.10 + 200, failed and cancelled ones are not counted
    assert stats.volume == Decimal("410.00")
    assert stats.fees == Decimal("2.00")
    assert stats.success_rate == Decimal("0.75")


def test_statistics_of_nothing(bank):
    stats = TransactionReport(bank, [], RATES).statistics()
    assert (stats.total, stats.volume, stats.success_rate) == (0, Decimal("0.00"), Decimal(0))


def test_total_balance_converts_every_currency(bank):
    # 1 000 + 1 000 EUR * 1.10 + 500 + 100 000 RUB * 0.01 + 1 500
    assert TransactionReport(bank, [], RATES).total_balance() == Decimal("5100.00")


def test_top_clients_across_currencies(bank):
    report = TransactionReport(bank, [], RATES)
    top = report.top_clients(2)
    # John 1 000 + 1 100, Mary 500 + 1 000, Anna 1 500: Anna before Mary by name
    assert [(r.position, r.client.client_id, r.total) for r in top] == [
        (1, "CL-001", Decimal("2100.00")),
        (2, "CL-003", Decimal("1500.00")),
    ]
    assert len(report.top_clients()) == 3


def test_top_clients_count_overdraft_as_negative(bank):
    process(bank, [make_tx("withdrawal", 2_000, sender_id="M-PRM")])
    top = TransactionReport(bank, [], RATES).top_clients()
    # Mary: -1 500 USD + 1 000 USD from roubles
    assert (top[-1].client.client_id, top[-1].total) == ("CL-002", Decimal("-500.00"))
    assert TransactionReport(bank, [], RATES).total_balance() == Decimal("3100.00")


@pytest.mark.parametrize("limit", [0, -1, 1.5, True])
def test_top_clients_rejects_bad_limit(bank, limit):
    with pytest.raises(InvalidOperationError):
        TransactionReport(bank, [], RATES).top_clients(limit)


def test_missing_rate_is_an_error(bank):
    with pytest.raises(InvalidOperationError):
        TransactionReport(bank, [], ExchangeRates({"EUR": "1.10"})).total_balance()


@pytest.mark.parametrize(
    "kwargs",
    [{"bank": "bank"}, {"rates": {"EUR": 1.1}}, {"transactions": ["T-1"]}],
)
def test_report_rejects_bad_arguments(bank, kwargs):
    arguments = {"bank": bank, "transactions": [], "rates": RATES, **kwargs}
    with pytest.raises(InvalidOperationError):
        TransactionReport(**arguments)


def test_render(bank):
    transactions = process(bank, [make_tx("deposit", 100, receiver_id="J-USD")])
    text = TransactionReport(bank, transactions, RATES).render()
    assert "Top-3 clients (USD, all currencies):\n  1. John Smith: 2 200.00" in text
    assert "total 1: pending 0, completed 1, failed 0, cancelled 0" in text
    assert "success rate 100.00%, volume 100.00 USD, fees 0.00 USD" in text
    assert "  EUR: 1 000.00" in text
    assert "  KZT" not in text  # currencies without accounts are left out
    assert "all accounts in USD: 5 200.00" in text


# --- The Day 6 simulation ---


def test_simulation_meets_the_requirements(capsys):
    import main

    main.demo_simulation()
    out = capsys.readouterr().out
    assert "Training Bank: 7 clients, 13 accounts" in out
    assert "39 transactions queued" in out
    for event in ("transaction_completed", "transaction_failed", "transaction_blocked"):
        assert event in out
    assert "total 39: pending 0, completed 27, failed 11, cancelled 1" in out
    assert "=== Simulation: client John Smith ===" in out
    assert "1. Mary Williams" in out
    assert "all accounts in USD:" in out
