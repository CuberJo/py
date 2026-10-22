import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

# Make modules from src/ importable when running pytest from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from audit import AuditLevel, AuditLog  # noqa: E402
from bank import Bank  # noqa: E402
from clients import Client  # noqa: E402
from exceptions import (  # noqa: E402
    AccountNotFoundError,
    ClientNotFoundError,
    InvalidOperationError,
)
from reports import AuditReport  # noqa: E402
from risk import RiskAnalyzer, RiskFactor, RiskLevel  # noqa: E402
from transactions import (  # noqa: E402
    ExchangeRates,
    Transaction,
    TransactionProcessor,
    TransactionQueue,
    TransactionStatus,
)

TODAY = date(2026, 10, 22)
DAYTIME = datetime(2026, 10, 22, 14, 0)
RATES = ExchangeRates({"EUR": "1.10"})


class FakeClock:
    """A clock the tests can move to any time of day."""

    def __init__(self, now: datetime = DAYTIME):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_tx(transaction_type, amount, currency="USD", *, at=DAYTIME, **kwargs) -> Transaction:
    return Transaction(transaction_type, amount, currency, created_at=at, **kwargs)


def at(hour: int, minute: int = 0, day: int = 22) -> datetime:
    return datetime(2026, 10, day, hour, minute)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def audit_log(clock: FakeClock) -> AuditLog:
    return AuditLog(clock=clock)


@pytest.fixture
def bank(clock: FakeClock, audit_log: AuditLog) -> Bank:
    bank = Bank("Training Bank", clock=clock, audit_log=audit_log)
    for name, client_id in (("John Smith", "C-1"), ("Mary Williams", "C-2")):
        bank.add_client(Client(name, date(1990, 5, 17), "1234", client_id=client_id, today=TODAY))
    bank.open_account("C-1", account_id="J-USD", balance=500_000)
    bank.open_account("C-1", account_id="J-EUR", balance=20_000, currency="EUR")
    bank.open_account("C-2", account_id="M-USD", balance=5_000)
    return bank


@pytest.fixture
def analyzer(bank: Bank) -> RiskAnalyzer:
    return RiskAnalyzer(bank, rates=RATES)


@pytest.fixture
def processor(bank: Bank, audit_log: AuditLog, analyzer: RiskAnalyzer) -> TransactionProcessor:
    return TransactionProcessor(bank, rates=RATES, audit_log=audit_log, risk_analyzer=analyzer)


def balance(bank, account_id):
    return bank.get_account(account_id).balance


# --- Risk factors ---


def test_ordinary_operation_is_low_risk(analyzer):
    assessment = analyzer.assess(make_tx("deposit", 500, receiver_id="J-USD"))
    assert assessment.level is RiskLevel.LOW
    assert assessment.factors == ()
    assert assessment.score == 0
    assert assessment.client_id == "C-1"
    assert not assessment.blocked


@pytest.mark.parametrize(
    "amount, currency, factor, level",
    [
        ("9999.99", "USD", None, RiskLevel.LOW),
        (10_000, "USD", RiskFactor.LARGE_AMOUNT, RiskLevel.MEDIUM),
        (9_100, "EUR", RiskFactor.LARGE_AMOUNT, RiskLevel.MEDIUM),  # 10 010 USD
        (100_000, "USD", RiskFactor.VERY_LARGE_AMOUNT, RiskLevel.HIGH),
    ],
)
def test_large_amounts(analyzer, amount, currency, factor, level):
    assessment = analyzer.assess(make_tx("withdrawal", amount, currency, sender_id="J-USD"))
    assert assessment.factors == ((factor,) if factor else ())
    assert assessment.level is level
    assert assessment.blocked is (level is RiskLevel.HIGH)


def test_amount_without_rate_is_compared_as_is(bank):
    analyzer = RiskAnalyzer(bank)  # no rates: 10 000 EUR counts as 10 000
    assessment = analyzer.assess(make_tx("withdrawal", 10_000, "EUR", sender_id="J-EUR"))
    assert assessment.factors == (RiskFactor.LARGE_AMOUNT,)


def test_frequent_operations(analyzer):
    # Three operations within ten minutes are fine, the fourth is flagged
    for minute in (0, 3, 6):
        tx = make_tx("deposit", 10, receiver_id="M-USD", at=at(13, minute))
        assert analyzer.check(tx).factors == ()
    fourth = make_tx("deposit", 10, receiver_id="M-USD", at=at(13, 9))
    assert analyzer.assess(fourth).factors == (RiskFactor.FREQUENT_OPERATIONS,)
    assert analyzer.assess(fourth).level is RiskLevel.MEDIUM
    # 13:00 is outside the window of 13:11, so only two earlier operations count
    later = make_tx("deposit", 10, receiver_id="M-USD", at=at(13, 11))
    assert analyzer.assess(later).factors == ()
    # Another client's operations do not count
    other = make_tx("deposit", 10, receiver_id="J-USD", at=at(13, 9))
    assert analyzer.assess(other).factors == ()


def test_assess_does_not_record(analyzer):
    for minute in range(5):
        analyzer.assess(make_tx("deposit", 10, receiver_id="M-USD", at=at(13, minute)))
    assert analyzer.assessments() == []
    tx = make_tx("deposit", 10, receiver_id="M-USD", at=at(13, 5))
    assert analyzer.assess(tx).factors == ()


def test_new_receiver(bank, analyzer, processor):
    first = make_tx("transfer", 100, sender_id="J-USD", receiver_id="M-USD")
    assert analyzer.assess(first).factors == (RiskFactor.NEW_RECEIVER,)
    assert analyzer.assess(first).level is RiskLevel.LOW
    processor.process(first)
    # Once money went there successfully, the receiver is known
    second = make_tx("transfer", 100, sender_id="J-USD", receiver_id="M-USD")
    assert analyzer.assess(second).factors == ()
    # Mary has never sent money to John
    back = make_tx("transfer", 100, sender_id="M-USD", receiver_id="J-USD")
    assert analyzer.assess(back).factors == (RiskFactor.NEW_RECEIVER,)


def test_new_external_receiver_and_failed_transfer(analyzer, processor):
    failed = processor.process(
        make_tx("external_transfer", 9_000, sender_id="M-USD", receiver_id="GB-0001")
    )
    assert failed.status is TransactionStatus.FAILED  # not enough money
    # A failed transfer does not make the receiver known
    again = make_tx("external_transfer", 10, sender_id="M-USD", receiver_id="GB-0001")
    assert analyzer.assess(again).factors == (RiskFactor.NEW_RECEIVER,)


@pytest.mark.parametrize(
    "tx",
    [
        make_tx("transfer", 100, sender_id="J-USD", receiver_id="J-EUR"),  # own account
        make_tx("deposit", 100, receiver_id="J-USD"),
        make_tx("withdrawal", 100, sender_id="J-USD"),
    ],
)
def test_not_a_new_receiver(analyzer, tx):
    assert RiskFactor.NEW_RECEIVER not in analyzer.assess(tx).factors


@pytest.mark.parametrize(
    "moment, is_night",
    [
        (at(22, 59), False),
        (at(23, 0), True),
        (at(2, 30), True),
        (at(5, 59), True),
        (at(6, 0), False),
        (at(14, 0), False),
    ],
)
def test_night_time(analyzer, moment, is_night):
    assessment = analyzer.assess(make_tx("deposit", 10, receiver_id="J-USD", at=moment))
    assert (RiskFactor.NIGHT_TIME in assessment.factors) is is_night


def test_factors_add_up_to_high_risk(analyzer):
    # Large (2) + new receiver (1) + night (1) = 4
    tx = make_tx(
        "external_transfer", 10_000, sender_id="J-USD", receiver_id="FR-0002", at=at(2, 30)
    )
    assessment = analyzer.assess(tx)
    assert set(assessment.factors) == {
        RiskFactor.LARGE_AMOUNT,
        RiskFactor.NEW_RECEIVER,
        RiskFactor.NIGHT_TIME,
    }
    assert (assessment.score, assessment.level, assessment.blocked) == (4, RiskLevel.HIGH, True)
    assert "BLOCKED" in str(assessment)


def test_level_for_score(analyzer):
    assert [analyzer.level_for(score).value for score in range(6)] == [
        "low", "low", "medium", "medium", "high", "high",
    ]  # fmt: skip


def test_unknown_account(analyzer):
    with pytest.raises(AccountNotFoundError):
        analyzer.assess(make_tx("deposit", 10, receiver_id="NOPE"))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"large_amount": 0},
        {"large_amount": 100, "very_large_amount": 100},
        {"frequency_limit": 0},
        {"frequency_limit": True},
        {"frequency_window": timedelta(0)},
        {"block_level": "extreme"},
        {"rates": {"EUR": 1}},
    ],
)
def test_analyzer_rejects_bad_settings(bank, kwargs):
    with pytest.raises(InvalidOperationError):
        RiskAnalyzer(bank, **kwargs)


def test_analyzer_needs_a_bank():
    with pytest.raises(InvalidOperationError):
        RiskAnalyzer("bank")


# --- Blocking in the processor ---


def test_high_risk_is_blocked_without_moving_money(bank, processor, audit_log):
    tx = processor.process(
        make_tx("transfer", 120_000, sender_id="J-USD", receiver_id="M-USD", transaction_id="T-1")
    )
    assert tx.status is TransactionStatus.FAILED
    assert tx.failure_reason.startswith("SuspiciousOperationError")
    assert "very large amount" in tx.failure_reason
    assert tx.attempts == 1  # not retried
    assert balance(bank, "J-USD") == Decimal("500000.00")
    assert balance(bank, "M-USD") == Decimal("5000.00")

    [entry] = audit_log.filter(level="critical")
    assert (entry.event, entry.transaction_id, entry.client_id, entry.account_id) == (
        "transaction_blocked",
        "T-1",
        "C-1",
        "J-USD",
    )
    assert entry.details["error"] == "SuspiciousOperationError"
    assert processor.error_log[0].error == "SuspiciousOperationError"


def test_medium_risk_is_allowed_with_a_warning(bank, processor, audit_log):
    tx = processor.process(make_tx("withdrawal", 15_000, sender_id="J-USD", transaction_id="T-1"))
    assert tx.status is TransactionStatus.COMPLETED
    assert balance(bank, "J-USD") == Decimal("485000.00")
    assert [(e.level, e.event) for e in audit_log.entries] == [
        (AuditLevel.WARNING, "risk_flagged"),
        (AuditLevel.INFO, "transaction_completed"),
    ]
    assert audit_log.entries[0].details["risk_level"] == "medium"
    assert audit_log.entries[0].details["risk_factors"] == ["large amount"]


def test_low_risk_factor_is_logged_as_info(processor, audit_log):
    processor.process(make_tx("transfer", 100, sender_id="J-USD", receiver_id="M-USD"))
    assert [(e.level, e.event) for e in audit_log.entries] == [
        (AuditLevel.INFO, "risk_flagged"),
        (AuditLevel.INFO, "transaction_completed"),
    ]


def test_block_level_can_be_lowered(bank, audit_log):
    strict = RiskAnalyzer(bank, block_level="medium")
    processor = TransactionProcessor(bank, audit_log=audit_log, risk_analyzer=strict)
    tx = processor.process(make_tx("withdrawal", 15_000, sender_id="J-USD"))
    assert tx.status is TransactionStatus.FAILED
    assert "medium risk" in tx.failure_reason


def test_night_retry_is_assessed_once_per_attempt(bank, processor, audit_log, clock, analyzer):
    clock.now = at(4, 0, day=23)
    queue = TransactionQueue(clock=clock)
    tx = queue.add(make_tx("deposit", 100, receiver_id="J-USD", at=at(4, 0, day=23)))
    for moment in (at(4, 0, day=23), at(4, 30, day=23), at(5, 0, day=23)):
        clock.now = moment
        processor.run(queue)
    assert tx.status is TransactionStatus.COMPLETED
    # The client made one operation, so it is listed once
    assert len(analyzer.assessments()) == 1
    retries = audit_log.filter(event="transaction_retry")
    assert [e.details["attempt"] for e in retries] == [1, 2]
    # The bank flagged both night attempts too
    assert len(audit_log.filter(event="suspicious_activity")) == 2


def test_processor_rejects_bad_audit_settings(bank):
    with pytest.raises(InvalidOperationError):
        TransactionProcessor(bank, audit_log="audit.log")
    with pytest.raises(InvalidOperationError):
        TransactionProcessor(bank, risk_analyzer=object())


def test_processor_without_analyzer_does_not_block(bank):
    processor = TransactionProcessor(bank)
    tx = processor.process(make_tx("withdrawal", 150_000, sender_id="J-USD"))
    assert tx.status is TransactionStatus.COMPLETED


# --- Ordinary and suspicious transactions end to end ---

# (id, created at, transaction arguments, expected level, expected status)
SCENARIO = [
    # John, at night: an external transfer to a new receiver
    ("T-01", at(23, 40, day=21), ("external_transfer", 500), {"sender_id": "J-USD", "receiver_id": "DE-0001"}, "medium", "completed"),  # noqa: E501
    ("T-02", at(2, 30), ("external_transfer", 10_000), {"sender_id": "J-USD", "receiver_id": "FR-0002"}, "high", "failed"),  # noqa: E501
    # Ordinary daytime operations
    ("T-03", at(9, 0), ("deposit", 500), {"receiver_id": "J-USD"}, "low", "completed"),
    ("T-04", at(10, 0), ("transfer", 300), {"sender_id": "J-USD", "receiver_id": "M-USD"}, "low", "completed"),  # noqa: E501
    ("T-05", at(11, 0), ("transfer", 200), {"sender_id": "J-USD", "receiver_id": "M-USD"}, "low", "completed"),  # noqa: E501
    ("T-06", at(12, 0), ("transfer", 1_000, "EUR"), {"sender_id": "J-EUR", "receiver_id": "J-USD"}, "low", "completed"),  # noqa: E501
    ("T-07", at(12, 30), ("withdrawal", 9_000), {"sender_id": "M-USD"}, "low", "failed"),
    ("T-08", at(13, 0), ("withdrawal", 2_000), {"sender_id": "M-USD"}, "low", "completed"),
    # John: large amounts
    ("T-09", at(13, 30), ("withdrawal", 15_000), {"sender_id": "J-USD"}, "medium", "completed"),
    ("T-10", at(13, 35), ("transfer", 120_000), {"sender_id": "J-USD", "receiver_id": "M-USD"}, "high", "failed"),  # noqa: E501
    ("T-11", at(13, 40), ("external_transfer", 30_000), {"sender_id": "J-USD", "receiver_id": "DE-0001"}, "medium", "completed"),  # noqa: E501
    # Mary: a burst of operations
    ("T-12", at(13, 50), ("deposit", 100), {"receiver_id": "M-USD"}, "low", "completed"),
    ("T-13", at(13, 52), ("deposit", 100), {"receiver_id": "M-USD"}, "low", "completed"),
    ("T-14", at(13, 54), ("deposit", 100), {"receiver_id": "M-USD"}, "low", "completed"),
    ("T-15", at(13, 56), ("transfer", 1_000), {"sender_id": "M-USD", "receiver_id": "J-USD"}, "medium", "completed"),  # noqa: E501
    ("T-16", at(13, 58), ("external_transfer", 11_000), {"sender_id": "M-USD", "receiver_id": "GB-0003"}, "high", "failed"),  # noqa: E501
]  # fmt: skip


@pytest.fixture
def scenario(bank, processor, clock):
    queue = TransactionQueue(clock=clock)
    for tx_id, created, args, kwargs, _, _ in SCENARIO:
        queue.add(make_tx(*args, at=created, transaction_id=tx_id, **kwargs))
    return {tx.transaction_id: tx for tx in processor.run(queue)}


def test_scenario_levels_and_statuses(bank, analyzer, scenario):
    levels = {a.transaction_id: a.level.value for a in analyzer.assessments()}
    statuses = {tx_id: tx.status.value for tx_id, tx in scenario.items()}
    assert levels == {tx_id: level for tx_id, _, _, _, level, _ in SCENARIO}
    assert statuses == {tx_id: status for tx_id, _, _, _, _, status in SCENARIO}
    # Every high-risk transaction was blocked, and only those
    blocked = {tx_id for tx_id, tx in scenario.items() if "Suspicious" in (tx.failure_reason or "")}
    assert blocked == {"T-02", "T-10", "T-16"}
    assert scenario["T-07"].failure_reason.startswith("InsufficientFundsError")

    # 500 000 - 505 + 500 - 300 - 200 + 1 100 - 15 000 - 30 300 + 1 000
    assert balance(bank, "J-USD") == Decimal("456295.00")
    assert balance(bank, "J-EUR") == Decimal("19000.00")
    # 5 000 + 300 + 200 - 2 000 + 300 - 1 000
    assert balance(bank, "M-USD") == Decimal("2800.00")


def test_scenario_factors(analyzer, scenario):
    factors = {a.transaction_id: set(a.factors) for a in analyzer.assessments()}
    assert factors["T-01"] == {RiskFactor.NIGHT_TIME, RiskFactor.NEW_RECEIVER}
    assert factors["T-04"] == {RiskFactor.NEW_RECEIVER}
    assert factors["T-05"] == set()  # Mary is known after T-04
    assert factors["T-11"] == {RiskFactor.LARGE_AMOUNT}  # DE-0001 is known after T-01
    assert factors["T-14"] == set()  # only two earlier operations in the window
    assert factors["T-15"] == {RiskFactor.FREQUENT_OPERATIONS, RiskFactor.NEW_RECEIVER}
    assert factors["T-16"] == {
        RiskFactor.FREQUENT_OPERATIONS,
        RiskFactor.LARGE_AMOUNT,
        RiskFactor.NEW_RECEIVER,
    }


def test_report_suspicious_operations(audit_log, analyzer, scenario):
    report = AuditReport(audit_log, analyzer)
    suspicious = report.suspicious_operations()
    assert [(a.transaction_id, a.score) for a in suspicious] == [
        ("T-16", 5),
        ("T-02", 4),
        ("T-10", 4),
        ("T-15", 3),
        ("T-01", 2),
        ("T-09", 2),
        ("T-11", 2),
    ]
    assert [a.transaction_id for a in report.suspicious_operations("high")] == [
        "T-16",
        "T-02",
        "T-10",
    ]


def test_report_client_risk_profile(audit_log, analyzer, scenario):
    report = AuditReport(audit_log, analyzer)
    john = report.client_risk_profile("C-1")
    assert john.operations == 9
    assert john.by_level == {RiskLevel.LOW: 4, RiskLevel.MEDIUM: 3, RiskLevel.HIGH: 2}
    assert (john.blocked, john.max_score, john.level) == (2, 4, RiskLevel.HIGH)
    assert john.factors[RiskFactor.LARGE_AMOUNT] == 3
    assert john.factors[RiskFactor.NIGHT_TIME] == 2

    mary = report.client_risk_profile("C-2")
    assert mary.operations == 7
    assert mary.by_level == {RiskLevel.LOW: 5, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 1}
    assert (mary.blocked, mary.max_score) == (1, 5)
    assert mary.factors[RiskFactor.FREQUENT_OPERATIONS] == 2

    # Riskiest first: both are high, Mary has the higher score
    assert [p.client_id for p in report.client_risk_profiles()] == ["C-2", "C-1"]
    with pytest.raises(ClientNotFoundError):
        report.client_risk_profile("C-404")


def test_profile_of_a_client_without_operations(bank, analyzer):
    bank.add_client(Client("Anna Lee", date(1995, 1, 1), "1234", client_id="C-3", today=TODAY))
    profile = analyzer.get_client_profile("C-3")
    assert (profile.operations, profile.level, profile.max_score) == (0, RiskLevel.LOW, 0)


def test_report_error_statistics(audit_log, analyzer, scenario):
    stats = AuditReport(audit_log, analyzer).error_statistics()
    assert (stats.attempts, stats.errors, stats.failed, stats.blocked, stats.retried) == (
        16,
        4,
        1,
        3,
        0,
    )
    assert stats.by_error == {"SuspiciousOperationError": 3, "InsufficientFundsError": 1}
    assert stats.error_rate == Decimal("0.25")


def test_report_render(audit_log, analyzer, scenario):
    text = AuditReport(audit_log, analyzer).render()
    assert "Suspicious operations:" in text
    assert "T-16 | client C-2 | high risk (score 5) | BLOCKED" in text
    assert "C-1: high risk | operations 9" in text
    assert "attempts 16, errors 4 (25.00%)" in text
    assert "C-2" not in AuditReport(audit_log, analyzer).render("C-1").split("profiles:")[1]


def test_report_without_data(audit_log, analyzer):
    report = AuditReport(audit_log, analyzer)
    assert report.suspicious_operations() == []
    assert report.error_statistics().error_rate == 0
    assert report.render().count("none") == 2


def test_report_rejects_bad_arguments(audit_log, analyzer):
    with pytest.raises(InvalidOperationError):
        AuditReport([], analyzer)
    with pytest.raises(InvalidOperationError):
        AuditReport(audit_log, None)
