import json
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

# Make modules from src/ importable when running pytest from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from audit import AuditEntry, AuditLevel, AuditLog  # noqa: E402
from bank import Bank  # noqa: E402
from clients import Client  # noqa: E402
from exceptions import AuthenticationError, InvalidOperationError  # noqa: E402

DAYTIME = datetime(2026, 10, 22, 14, 0)


class FakeClock:
    """A clock the tests can move to any time of day."""

    def __init__(self, now: datetime = DAYTIME):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def log(clock: FakeClock) -> AuditLog:
    log = AuditLog(clock=clock)
    log.log("info", "login", "John logged in", client_id="C-1")
    clock.now += timedelta(minutes=5)
    log.log(AuditLevel.WARNING, "risk_flagged", "Large amount", client_id="C-1", account_id="A-1")
    clock.now += timedelta(minutes=5)
    log.log("error", "transaction_failed", "No money", client_id="C-2", transaction_id="T-1")
    clock.now += timedelta(minutes=5)
    log.log("CRITICAL", "transaction_blocked", "High risk", client_id="C-2", transaction_id="T-2")
    return log


def test_levels_are_ordered():
    assert [level.severity for level in AuditLevel] == [0, 1, 2, 3]
    assert AuditLevel.parse("Warning") is AuditLevel.WARNING
    with pytest.raises(InvalidOperationError):
        AuditLevel.parse("fatal")


def test_log_keeps_entries_in_memory(log):
    assert len(log) == 4
    first = log.entries[0]
    assert (first.timestamp, first.level, first.event, first.client_id) == (
        DAYTIME,
        AuditLevel.INFO,
        "login",
        "C-1",
    )
    assert "INFO" in str(first) and "client C-1" in str(first)
    # The returned list is a copy
    log.entries.clear()
    assert len(log) == 4


def test_explicit_timestamp(log):
    entry = log.log("info", "x", "msg", timestamp=datetime(2020, 1, 1))
    assert entry.timestamp == datetime(2020, 1, 1)


@pytest.mark.parametrize("kwargs", [{"level": "loud"}, {"event": " "}])
def test_log_rejects_bad_input(log, kwargs):
    args = {"level": "info", "event": "x", "message": "msg", **kwargs}
    with pytest.raises(InvalidOperationError):
        log.log(args["level"], args["event"], args["message"])


def test_filter(log):
    events = lambda entries: [e.event for e in entries]  # noqa: E731
    assert events(log.filter(level="warning")) == ["risk_flagged"]
    assert events(log.filter(min_level="error")) == ["transaction_failed", "transaction_blocked"]
    assert events(log.filter(client_id="C-1")) == ["login", "risk_flagged"]
    assert events(log.filter(account_id="A-1")) == ["risk_flagged"]
    assert events(log.filter(transaction_id="T-2")) == ["transaction_blocked"]
    assert events(log.filter(event="login")) == ["login"]
    window = log.filter(since=DAYTIME + timedelta(minutes=5), until=DAYTIME + timedelta(minutes=10))
    assert events(window) == ["risk_flagged", "transaction_failed"]
    assert log.filter(client_id="C-2", min_level="critical")[0].transaction_id == "T-2"
    assert log.filter(client_id="C-9") == []


def test_file_storage_and_load(tmp_path, clock):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, clock=clock)
    log.log(
        "warning",
        "risk_flagged",
        "Large amount",
        client_id="C-1",
        details={"amount": Decimal("15000.00"), "factors": ["large amount"]},
    )
    log.log("info", "login", "Привет")

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["details"] == {"amount": "15000.00", "factors": ["large amount"]}

    loaded = AuditLog.load(path, clock=clock)
    assert [e.event for e in loaded.entries] == ["risk_flagged", "login"]
    assert loaded.entries[0].level is AuditLevel.WARNING
    assert loaded.entries[0].timestamp == DAYTIME
    assert loaded.entries[1].message == "Привет"
    # New entries are appended to the same file
    loaded.log("error", "transaction_failed", "x")
    assert len(path.read_text(encoding="utf-8").splitlines()) == 3


def test_load_rejects_broken_file(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text('{"timestamp": "2026-10-22T14:00:00"}\n', encoding="utf-8")
    with pytest.raises(InvalidOperationError, match="line 1"):
        AuditLog.load(path)


def test_entry_round_trip():
    entry = AuditEntry(DAYTIME, AuditLevel.ERROR, "e", "m", "C-1", "A-1", "T-1", {"n": 1})
    assert AuditEntry.from_dict(entry.to_dict()) == entry


def test_bank_writes_suspicious_activity_to_audit_log(clock):
    log = AuditLog(clock=clock)
    bank = Bank("Training Bank", clock=clock, audit_log=log)
    bank.add_client(
        Client("John Smith", date(1990, 5, 17), "1234", client_id="C-1", today=date(2026, 10, 22))
    )
    with pytest.raises(AuthenticationError):
        bank.authenticate_client("C-404", "1234")
    [entry] = log.entries
    assert (entry.level, entry.event, entry.client_id) == (
        AuditLevel.WARNING,
        "suspicious_activity",
        "C-404",
    )
    assert entry.message == bank.get_suspicious_activities()[0].reason


def test_bank_rejects_bad_audit_log():
    with pytest.raises(InvalidOperationError):
        Bank("Training Bank", audit_log=[])
