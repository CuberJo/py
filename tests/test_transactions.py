import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

# Make modules from src/ importable when running pytest from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from accounts import PremiumAccount, SavingsAccount  # noqa: E402
from bank import Bank  # noqa: E402
from clients import Client  # noqa: E402
from exceptions import InvalidOperationError, TransactionNotFoundError  # noqa: E402
from models import AccountStatus  # noqa: E402
from transactions import (  # noqa: E402
    ExchangeRates,
    Transaction,
    TransactionProcessor,
    TransactionQueue,
    TransactionStatus,
    TransactionType,
)

TODAY = date(2026, 10, 22)
DAYTIME = datetime(2026, 10, 22, 14, 0)
RATES = ExchangeRates({"EUR": "1.10", "RUB": "0.01"})


class FakeClock:
    """A clock the tests can move to any time of day."""

    def __init__(self, now: datetime = DAYTIME):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_tx(transaction_type, amount, currency="USD", **kwargs) -> Transaction:
    kwargs.setdefault("created_at", DAYTIME)
    return Transaction(transaction_type, amount, currency, **kwargs)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def bank(clock: FakeClock) -> Bank:
    bank = Bank("Training Bank", clock=clock)
    for name, client_id in (("John Smith", "C-1"), ("Mary Williams", "C-2")):
        bank.add_client(Client(name, date(1990, 5, 17), "1234", client_id=client_id, today=TODAY))
    bank.open_account("C-1", account_id="J-USD", balance=5_000)
    bank.open_account("C-1", account_id="J-EUR", balance=1_000, currency="EUR")
    bank.open_account("C-1", SavingsAccount, account_id="J-SAV", balance=2_000, min_balance=500)
    bank.open_account("C-2", PremiumAccount, account_id="M-PRM", balance=1_000, fee=0)
    bank.open_account("C-2", account_id="M-USD", balance=100)
    return bank


@pytest.fixture
def processor(bank: Bank) -> TransactionProcessor:
    return TransactionProcessor(bank, rates=RATES)


@pytest.fixture
def queue(clock: FakeClock) -> TransactionQueue:
    return TransactionQueue(clock=clock)


def balance(bank, account_id):
    return bank.get_account(account_id).balance


# --- Transaction ---


def test_transaction_fields():
    tx = make_tx(
        "Transfer", "250.50", "eur", sender_id="J-EUR", receiver_id="J-USD", transaction_id="T-1"
    )
    assert tx.transaction_id == "T-1"
    assert tx.transaction_type is TransactionType.TRANSFER
    assert tx.amount == Decimal("250.50")
    assert tx.currency.value == "EUR"
    assert tx.fee == Decimal("0.00")
    assert tx.status is TransactionStatus.PENDING
    assert tx.failure_reason is None
    assert tx.created_at == DAYTIME
    assert tx.processed_at is None
    assert tx.get_transaction_info()["status"] == "pending"
    assert "****-USD" in str(tx)


def test_transaction_generates_id():
    tx = make_tx("deposit", 1, receiver_id="J-USD", id_generator=lambda: "GEN-1")
    assert tx.transaction_id == "GEN-1"


@pytest.mark.parametrize(
    "transaction_type, kwargs",
    [
        ("deposit", {}),
        ("deposit", {"receiver_id": "J-USD", "sender_id": "M-USD"}),
        ("withdrawal", {"receiver_id": "J-USD"}),
        ("transfer", {"sender_id": "J-USD"}),
        ("transfer", {"sender_id": "J-USD", "receiver_id": "J-USD"}),
        ("external_transfer", {"sender_id": "J-USD", "receiver_id": " "}),
        ("refund", {"receiver_id": "J-USD"}),
        ("deposit", {"receiver_id": "J-USD", "priority": "high"}),
        ("deposit", {"receiver_id": "J-USD", "scheduled_at": "tomorrow"}),
    ],
)
def test_transaction_rejects_invalid_data(transaction_type, kwargs):
    with pytest.raises(InvalidOperationError):
        make_tx(transaction_type, 100, **kwargs)


@pytest.mark.parametrize("amount", [0, -5, "abc", 1.005])
def test_transaction_rejects_invalid_amounts(amount):
    with pytest.raises(InvalidOperationError):
        make_tx("deposit", amount, receiver_id="J-USD")


def test_cancelled_transaction_is_final(processor):
    tx = make_tx("deposit", 100, receiver_id="J-USD")
    tx.cancel(DAYTIME)
    assert tx.status is TransactionStatus.CANCELLED
    with pytest.raises(InvalidOperationError):
        tx.cancel()
    with pytest.raises(InvalidOperationError):
        processor.process(tx)


# --- Queue ---


def test_queue_orders_by_priority_then_insertion(queue):
    low = queue.add(make_tx("deposit", 1, receiver_id="J-USD", transaction_id="LOW"))
    first = queue.add(make_tx("deposit", 1, receiver_id="J-USD", transaction_id="HI", priority=5))
    second = queue.add(make_tx("deposit", 1, receiver_id="J-USD", transaction_id="HI2", priority=5))
    assert queue.pending() == [first, second, low]
    assert [queue.pop_next(), queue.pop_next(), queue.pop_next(), queue.pop_next()] == [
        first,
        second,
        low,
        None,
    ]


def test_queue_holds_deferred_transactions(queue, clock):
    later = DAYTIME + timedelta(hours=2)
    tx = queue.add(make_tx("deposit", 1, receiver_id="J-USD", scheduled_at=later, priority=9))
    assert queue.ready() == []
    assert queue.pop_next() is None
    clock.now = later
    assert queue.pop_next() is tx
    assert len(queue) == 0


def test_queue_cancel(queue):
    tx = queue.add(make_tx("deposit", 1, receiver_id="J-USD", transaction_id="T-1"))
    assert "T-1" in queue
    assert queue.cancel("T-1", "Client changed their mind") is tx
    assert tx.status is TransactionStatus.CANCELLED
    assert tx.failure_reason == "Client changed their mind"
    assert tx.processed_at == DAYTIME
    assert "T-1" not in queue
    with pytest.raises(TransactionNotFoundError):
        queue.cancel("T-1")


def test_queue_rejects_duplicates_and_finished(queue):
    tx = queue.add(make_tx("deposit", 1, receiver_id="J-USD"))
    with pytest.raises(InvalidOperationError):
        queue.add(tx)
    done = make_tx("deposit", 1, receiver_id="J-USD")
    done.cancel()
    with pytest.raises(InvalidOperationError):
        queue.add(done)
    with pytest.raises(InvalidOperationError):
        queue.add("T-1")


# --- Exchange rates ---


def test_exchange_rates_convert():
    assert RATES.convert(100, "EUR", "USD") == Decimal("110.00")
    assert RATES.convert(110, "USD", "EUR") == Decimal("100.00")
    assert RATES.convert(100, "EUR", "RUB") == Decimal("11000.00")
    assert RATES.convert(5, "USD", "USD") == Decimal("5.00")
    with pytest.raises(InvalidOperationError):
        RATES.convert(1, "USD", "KZT")


@pytest.mark.parametrize("rates", [{"EUR": 0}, {"EUR": -1}, {"USD": 2}, {"GBP": 1}])
def test_exchange_rates_reject_bad_values(rates):
    with pytest.raises(InvalidOperationError):
        ExchangeRates(rates)


# --- Processor ---


def test_deposit_and_withdrawal(bank, processor):
    deposit = processor.process(make_tx("deposit", 500, receiver_id="J-USD"))
    withdrawal = processor.process(make_tx("withdrawal", 200, sender_id="J-USD"))
    assert deposit.status is withdrawal.status is TransactionStatus.COMPLETED
    assert deposit.credited_amount == Decimal("500.00")
    assert withdrawal.debited_amount == Decimal("200.00")
    assert withdrawal.processed_at == DAYTIME
    assert balance(bank, "J-USD") == Decimal("5300.00")


def test_transfer_converts_currency(bank, processor):
    tx = processor.process(make_tx("transfer", 100, "EUR", sender_id="J-EUR", receiver_id="J-USD"))
    assert tx.status is TransactionStatus.COMPLETED
    assert (tx.debited_amount, tx.credited_amount) == (Decimal("100.00"), Decimal("110.00"))
    assert balance(bank, "J-EUR") == Decimal("900.00")
    assert balance(bank, "J-USD") == Decimal("5110.00")


def test_transfer_without_rates_fails(bank):
    processor = TransactionProcessor(bank)
    tx = processor.process(make_tx("transfer", 100, sender_id="J-USD", receiver_id="J-EUR"))
    assert tx.status is TransactionStatus.FAILED
    assert "No exchange rates" in tx.failure_reason
    assert balance(bank, "J-USD") == Decimal("5000.00")


def test_external_transfer_fee(bank, processor):
    tx = processor.process(
        make_tx("external_transfer", 1_000, sender_id="J-USD", receiver_id="DE89370400440532")
    )
    assert tx.status is TransactionStatus.COMPLETED
    assert tx.fee == Decimal("10.00")
    assert (tx.debited_amount, tx.credited_amount) == (Decimal("1010.00"), Decimal("1000.00"))
    assert balance(bank, "J-USD") == Decimal("3990.00")


def test_internal_transfer_is_free(processor):
    tx = processor.process(make_tx("transfer", 1_000, sender_id="J-USD", receiver_id="M-USD"))
    assert tx.fee == Decimal("0.00")


def test_external_fee_in_another_currency(bank, processor):
    tx = processor.process(
        make_tx("external_transfer", 100, "EUR", sender_id="J-USD", receiver_id="FR7630006000")
    )
    # 100 EUR + 1% fee = 101 EUR = 111.10 USD
    assert tx.debited_amount == Decimal("111.10")
    assert balance(bank, "J-USD") == Decimal("4888.90")


def test_external_transfer_to_own_bank_is_rejected(processor):
    tx = processor.process(make_tx("external_transfer", 10, sender_id="J-USD", receiver_id="M-USD"))
    assert tx.status is TransactionStatus.FAILED


def test_negative_balance_only_for_premium(bank, processor):
    regular = processor.process(make_tx("transfer", 101, sender_id="M-USD", receiver_id="J-USD"))
    assert regular.status is TransactionStatus.FAILED
    assert regular.failure_reason.startswith("InsufficientFundsError")
    assert balance(bank, "M-USD") == Decimal("100.00")

    premium = processor.process(make_tx("transfer", 3_000, sender_id="M-PRM", receiver_id="J-USD"))
    assert premium.status is TransactionStatus.COMPLETED
    assert balance(bank, "M-PRM") == Decimal("-2000.00")
    # Already in the minus, the premium account can still send within its overdraft
    again = processor.process(make_tx("transfer", 500, sender_id="M-PRM", receiver_id="J-USD"))
    assert again.status is TransactionStatus.COMPLETED


def test_frozen_accounts_cannot_send_or_receive(bank, processor):
    bank.freeze_account("M-USD")
    outgoing = processor.process(make_tx("transfer", 10, sender_id="M-USD", receiver_id="J-USD"))
    incoming = processor.process(make_tx("transfer", 10, sender_id="J-USD", receiver_id="M-USD"))
    for tx in (outgoing, incoming):
        assert tx.status is TransactionStatus.FAILED
        assert tx.failure_reason.startswith("AccountFrozenError")
        assert tx.attempts == 1
    assert balance(bank, "J-USD") == Decimal("5000.00")


def test_account_rules_still_apply(bank, processor):
    # The savings account must keep its minimum balance of 500
    tx = processor.process(make_tx("withdrawal", 1_600, sender_id="J-SAV"))
    assert tx.status is TransactionStatus.FAILED
    assert balance(bank, "J-SAV") == Decimal("2000.00")


def test_unknown_account_fails_and_is_logged(processor):
    tx = processor.process(make_tx("deposit", 10, receiver_id="NOPE"))
    assert tx.status is TransactionStatus.FAILED
    [error] = processor.error_log
    assert (error.transaction_id, error.error, error.will_retry) == (
        tx.transaction_id,
        "AccountNotFoundError",
        False,
    )


def test_night_errors_are_retried(bank, processor, queue, clock):
    clock.now = datetime(2026, 10, 23, 4, 0)
    tx = queue.add(make_tx("transfer", 100, sender_id="J-USD", receiver_id="M-USD"))

    assert processor.run(queue) == [tx]
    assert tx.status is TransactionStatus.PENDING
    assert tx.scheduled_at == datetime(2026, 10, 23, 4, 30)
    assert tx in queue.pending()
    # Not due yet, so a second run at the same time does nothing
    assert processor.run(queue) == []

    clock.now = datetime(2026, 10, 23, 4, 30)
    processor.run(queue)
    clock.now = datetime(2026, 10, 23, 5, 0)
    processor.run(queue)
    assert tx.status is TransactionStatus.COMPLETED
    assert tx.attempts == 3
    assert [e.will_retry for e in processor.error_log] == [True, True]
    assert balance(bank, "M-USD") == Decimal("200.00")


def test_retries_stop_after_max_attempts(bank, clock, queue):
    processor = TransactionProcessor(bank, max_attempts=2, retry_delay=timedelta(minutes=10))
    clock.now = datetime(2026, 10, 23, 1, 0)
    tx = queue.add(make_tx("deposit", 10, receiver_id="J-USD"))
    processor.run(queue)
    clock.now += timedelta(minutes=10)
    processor.run(queue)
    assert tx.status is TransactionStatus.FAILED
    assert tx.failure_reason.startswith("OperationTimeError")
    assert len(queue) == 0
    assert [e.will_retry for e in processor.error_log] == [True, False]


def test_failed_credit_returns_money_to_sender(bank):
    # Bank.transfer itself is atomic, even without the processor's checks
    bank.get_account("M-USD").freeze()
    with pytest.raises(Exception):
        bank.transfer("M-PRM", "M-USD", 300, 300)
    assert balance(bank, "M-PRM") == Decimal("1000.00")


@pytest.mark.parametrize(
    "kwargs",
    [{"max_attempts": 0}, {"retry_delay": timedelta(0)}, {"external_fee_rate": 2}, {"rates": {}}],
)
def test_processor_rejects_bad_settings(bank, kwargs):
    with pytest.raises(InvalidOperationError):
        TransactionProcessor(bank, **kwargs)


# --- Ten transactions through the queue ---


def test_ten_transactions_through_the_queue(bank, processor, queue, clock):
    tomorrow_night = datetime(2026, 10, 23, 4, 30)
    transactions = [
        make_tx("deposit", 1_000, receiver_id="J-USD", transaction_id="T-01"),
        make_tx("transfer", 500, sender_id="J-USD", receiver_id="M-USD", transaction_id="T-02"),
        make_tx(
            "transfer", 200, "EUR", sender_id="J-EUR", receiver_id="J-USD", transaction_id="T-03"
        ),
        make_tx(
            "external_transfer",
            1_000,
            sender_id="J-USD",
            receiver_id="DE8937040044",
            transaction_id="T-04",
        ),
        make_tx("transfer", 3_000, sender_id="M-PRM", receiver_id="J-USD", transaction_id="T-05"),
        make_tx("transfer", 5_000, sender_id="M-USD", receiver_id="J-USD", transaction_id="T-06"),
        make_tx("deposit", 50, receiver_id="J-SAV", transaction_id="T-07"),
        make_tx("withdrawal", 100, sender_id="J-USD", priority=10, transaction_id="T-08"),
        make_tx(
            "deposit", 300, receiver_id="M-USD", scheduled_at=tomorrow_night, transaction_id="T-09"
        ),
        make_tx("deposit", 999, receiver_id="J-USD", transaction_id="T-10"),
    ]
    for tx in transactions:
        queue.add(tx)
    assert len(queue) == 10
    bank.freeze_account("J-SAV")
    queue.cancel("T-10")

    # Daytime: everything except the deferred T-09 runs, the urgent T-08 first
    done = processor.run(queue)
    assert [tx.transaction_id for tx in done] == [
        "T-08", "T-01", "T-02", "T-03", "T-04", "T-05", "T-06", "T-07",
    ]  # fmt: skip
    assert [tx.transaction_id for tx in queue.pending()] == ["T-09"]

    # T-09 comes due at night, so it is retried and goes through at 05:00
    clock.now = tomorrow_night
    processor.run(queue)
    clock.now = datetime(2026, 10, 23, 5, 0)
    processor.run(queue)
    assert len(queue) == 0

    status = {tx.transaction_id: tx.status for tx in transactions}
    assert status == {
        "T-01": TransactionStatus.COMPLETED,
        "T-02": TransactionStatus.COMPLETED,
        "T-03": TransactionStatus.COMPLETED,  # converted EUR -> USD
        "T-04": TransactionStatus.COMPLETED,  # external, 1% fee
        "T-05": TransactionStatus.COMPLETED,  # premium goes into the minus
        "T-06": TransactionStatus.FAILED,  # regular account cannot go into the minus
        "T-07": TransactionStatus.FAILED,  # frozen account
        "T-08": TransactionStatus.COMPLETED,
        "T-09": TransactionStatus.COMPLETED,  # deferred, then retried after the night
        "T-10": TransactionStatus.CANCELLED,
    }
    # 5 000 - 100 + 1 000 - 500 + 220 - 1 010 + 3 000
    assert balance(bank, "J-USD") == Decimal("7610.00")
    assert balance(bank, "J-EUR") == Decimal("800.00")
    assert balance(bank, "M-PRM") == Decimal("-2000.00")
    assert balance(bank, "M-USD") == Decimal("900.00")
    assert bank.get_account("J-SAV").status is AccountStatus.FROZEN
    assert transactions[3].fee == Decimal("10.00")
    assert [e.transaction_id for e in processor.error_log] == ["T-06", "T-07", "T-09"]
