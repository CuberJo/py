import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

# Make modules from src/ importable when running pytest from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from accounts import InvestmentAccount, PremiumAccount, SavingsAccount  # noqa: E402
from bank import Bank  # noqa: E402
from clients import Client, ClientStatus  # noqa: E402
from exceptions import (  # noqa: E402
    AccountFrozenError,
    AccountNotFoundError,
    AgeRestrictionError,
    AuthenticationError,
    ClientBlockedError,
    ClientNotFoundError,
    InvalidOperationError,
    OperationTimeError,
)
from models import AccountStatus, BankAccount, Currency  # noqa: E402

TODAY = date(2026, 10, 21)
DAYTIME = datetime(2026, 10, 21, 14, 0)


class FakeClock:
    """A clock the tests can move to any time of day."""

    def __init__(self, now: datetime = DAYTIME):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_client(name="John Smith", client_id="C-1", pin="1234", **kwargs) -> Client:
    kwargs.setdefault("birth_date", date(1990, 5, 17))
    return Client(name, pin=pin, client_id=client_id, today=TODAY, **kwargs)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def bank(clock: FakeClock) -> Bank:
    bank = Bank("Training Bank", clock=clock)
    bank.add_client(make_client("John Smith", "C-1", email="john@example.com"))
    bank.add_client(make_client("Mary Williams", "C-2", pin="5678"))
    return bank


@pytest.fixture
def account(bank: Bank) -> BankAccount:
    return bank.open_account("C-1", account_id="ACC-0001", balance=1_000)


# --- Client ---


def test_client_stores_data_and_contacts():
    client = make_client(phone="+7 (701) 123-45-67", email="john@example.com")
    assert client.client_id == "C-1"
    assert client.full_name == "John Smith"
    assert client.status is ClientStatus.ACTIVE
    assert client.contacts == {"phone": "+77011234567", "email": "john@example.com"}
    assert client.account_ids == ()
    assert client.age(TODAY) == 36


def test_client_generates_id():
    client = Client("Jo", date(1990, 1, 1), "1234", today=TODAY, id_generator=lambda: "GEN-1")
    assert client.client_id == "GEN-1"


@pytest.mark.parametrize(
    "birth_date, age",
    [(date(2008, 10, 21), 18), (date(2008, 10, 22), 17), (date(2010, 1, 1), 16)],
)
def test_client_age_limit(birth_date, age):
    if age >= 18:
        assert make_client(birth_date=birth_date).age(TODAY) == age
    else:
        with pytest.raises(AgeRestrictionError) as exc_info:
            make_client(birth_date=birth_date)
        assert exc_info.value.age == age


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "  "},
        {"birth_date": "1990-01-01"},
        {"birth_date": date(2030, 1, 1)},
        {"pin": "12"},
        {"pin": "12ab"},
        {"pin": 1234},
        {"phone": "123"},
        {"phone": "+7 701 abc 45 67"},
        {"email": "not-an-email"},
        {"client_id": " "},
    ],
)
def test_client_rejects_invalid_data(kwargs):
    with pytest.raises(InvalidOperationError):
        make_client(**kwargs)


def test_client_pin_is_not_stored_in_plain_text():
    client = make_client(pin="4321")
    assert client.check_pin("4321")
    assert not client.check_pin("1234")
    assert "4321" not in repr(vars(client))


def test_account_ids_are_read_only(bank, account):
    client = bank.get_client("C-1")
    assert client.account_ids == ("ACC-0001",)
    with pytest.raises(AttributeError):
        client.account_ids.append("HACK")


# --- Bank: clients and accounts ---


def test_add_client_rejects_duplicates_and_non_clients(bank):
    with pytest.raises(InvalidOperationError):
        bank.add_client(make_client("Other", "C-1"))
    with pytest.raises(InvalidOperationError):
        bank.add_client("C-3")
    assert len(bank.clients) == 2


def test_open_account_links_it_to_client(bank, account):
    assert account.owner.full_name == "John Smith"
    assert account.owner.email == "john@example.com"
    assert bank.get_account("ACC-0001") is account
    assert bank.get_client("C-1").account_ids == ("ACC-0001",)


def test_open_account_of_each_type(bank):
    savings = bank.open_account("C-1", SavingsAccount, balance=5_000, min_balance=1_000)
    premium = bank.open_account("C-2", PremiumAccount, currency="EUR")
    investment = bank.open_account("C-2", InvestmentAccount, balance=100)
    assert isinstance(savings, SavingsAccount) and savings.min_balance == Decimal("1000.00")
    assert premium.currency is Currency.EUR
    assert bank.get_client("C-2").account_ids == (premium.account_id, investment.account_id)


def test_open_account_errors(bank, account):
    with pytest.raises(ClientNotFoundError):
        bank.open_account("NOPE")
    with pytest.raises(InvalidOperationError):
        bank.open_account("C-1", dict)
    with pytest.raises(InvalidOperationError):
        bank.open_account("C-2", account_id="ACC-0001")
    assert bank.get_client("C-2").account_ids == ()


def test_unknown_account(bank):
    for action in (bank.close_account, bank.freeze_account, bank.unfreeze_account):
        with pytest.raises(AccountNotFoundError):
            action("NOPE")


def test_freeze_and_unfreeze_account(bank, account):
    bank.freeze_account("ACC-0001")
    assert account.status is AccountStatus.FROZEN
    with pytest.raises(AccountFrozenError):
        bank.deposit("ACC-0001", 100)
    bank.unfreeze_account("ACC-0001")
    assert bank.deposit("ACC-0001", 100) == Decimal("1100.00")


def test_close_account(bank, account):
    with pytest.raises(InvalidOperationError):
        bank.close_account("ACC-0001")
    bank.withdraw("ACC-0001", 1_000)
    bank.close_account("ACC-0001")
    assert account.status is AccountStatus.CLOSED
    # Closed accounts stay in the client's history
    assert bank.get_client("C-1").account_ids == ("ACC-0001",)


# --- Authentication ---


def test_authenticate_success(bank):
    assert bank.authenticate_client("C-1", "1234") is bank.get_client("C-1")


def test_three_wrong_pins_block_client(bank, account):
    for attempts_left in (2, 1):
        with pytest.raises(AuthenticationError) as exc_info:
            bank.authenticate_client("C-1", "0000")
        assert exc_info.value.attempts_left == attempts_left
    with pytest.raises(ClientBlockedError):
        bank.authenticate_client("C-1", "0000")

    client = bank.get_client("C-1")
    assert client.status is ClientStatus.BLOCKED
    # Even the right PIN does not help now
    with pytest.raises(ClientBlockedError):
        bank.authenticate_client("C-1", "1234")
    # A blocked client cannot operate, but can still freeze the account
    with pytest.raises(ClientBlockedError):
        bank.withdraw("ACC-0001", 10)
    with pytest.raises(ClientBlockedError):
        bank.open_account("C-1")
    bank.freeze_account("ACC-0001")
    assert any("Blocked" in a.reason for a in bank.get_suspicious_activities("C-1"))


def test_successful_login_resets_counter(bank):
    for _ in range(2):
        with pytest.raises(AuthenticationError):
            bank.authenticate_client("C-1", "0000")
    bank.authenticate_client("C-1", "1234")
    for _ in range(2):
        with pytest.raises(AuthenticationError):
            bank.authenticate_client("C-1", "0000")
    assert not bank.get_client("C-1").is_blocked


def test_failed_logins_are_counted_per_client(bank):
    for _ in range(2):
        with pytest.raises(AuthenticationError):
            bank.authenticate_client("C-1", "0000")
    with pytest.raises(AuthenticationError) as exc_info:
        bank.authenticate_client("C-2", "0000")
    assert exc_info.value.attempts_left == 2


def test_unblock_client(bank):
    for _ in range(3):
        with pytest.raises((AuthenticationError, ClientBlockedError)):
            bank.authenticate_client("C-1", "0000")
    bank.unblock_client("C-1")
    assert bank.authenticate_client("C-1", "1234").status is ClientStatus.ACTIVE


def test_unknown_client_login_is_flagged(bank):
    with pytest.raises(AuthenticationError) as exc_info:
        bank.authenticate_client("GHOST", "1234")
    assert exc_info.value.attempts_left is None
    [activity] = bank.get_suspicious_activities("GHOST")
    assert activity.timestamp == DAYTIME


# --- Night restriction and suspicious activity ---


@pytest.mark.parametrize("hour, minute", [(0, 0), (2, 30), (4, 59)])
def test_operations_blocked_at_night(bank, account, clock, hour, minute):
    clock.now = datetime(2026, 10, 22, hour, minute)
    for operation in (
        lambda: bank.open_account("C-1"),
        lambda: bank.deposit("ACC-0001", 100),
        lambda: bank.withdraw("ACC-0001", 100),
        lambda: bank.close_account("ACC-0001"),
    ):
        with pytest.raises(OperationTimeError):
            operation()
    assert account.balance == Decimal("1000.00")
    assert len(bank.get_suspicious_activities("C-1")) == 4


def test_freeze_allowed_at_night_but_unfreeze_is_not(bank, account, clock):
    clock.now = datetime(2026, 10, 22, 3, 0)
    bank.freeze_account("ACC-0001")
    with pytest.raises(OperationTimeError):
        bank.unfreeze_account("ACC-0001")
    clock.now = datetime(2026, 10, 22, 5, 0)
    bank.unfreeze_account("ACC-0001")


def test_login_allowed_at_night(bank, clock):
    clock.now = datetime(2026, 10, 22, 1, 0)
    bank.authenticate_client("C-1", "1234")


def test_large_operations_are_flagged(bank, account):
    bank.deposit("ACC-0001", 999_999)
    assert bank.get_suspicious_activities() == []
    bank.deposit("ACC-0001", 1_000_000)
    bank.withdraw("ACC-0001", 1_500_000)
    reasons = [a.reason for a in bank.get_suspicious_activities("C-1")]
    assert reasons == ["Large deposit: 1 000 000.00 USD", "Large withdrawal: 1 500 000.00 USD"]
    assert all(a.account_id == "ACC-0001" for a in bank.get_suspicious_activities())


# --- Search and reports ---


@pytest.fixture
def filled_bank(bank: Bank) -> Bank:
    bank.add_client(make_client("Robert Miller", "C-3"))
    bank.open_account("C-1", account_id="J-USD-1", balance=1_000)
    bank.open_account("C-1", SavingsAccount, account_id="J-USD-2", balance=4_000)
    bank.open_account("C-1", account_id="J-EUR-1", balance=700, currency="EUR")
    investment = bank.open_account("C-2", InvestmentAccount, account_id="M-USD-1", balance=10_000)
    investment.invest("stocks", 6_000)
    bank.open_account("C-3", PremiumAccount, account_id="R-USD-1", balance=10_000)
    bank.open_account("C-3", account_id="R-USD-2")
    bank.close_account("R-USD-2")
    return bank


def ids(accounts):
    return [a.account_id for a in accounts]


def test_search_accounts(filled_bank):
    assert ids(filled_bank.search_accounts(client_id="C-1")) == ["J-USD-1", "J-USD-2", "J-EUR-1"]
    assert ids(filled_bank.search_accounts(currency="eur")) == ["J-EUR-1"]
    assert ids(filled_bank.search_accounts(status="closed")) == ["R-USD-2"]
    assert ids(filled_bank.search_accounts(account_type=SavingsAccount)) == ["J-USD-2"]
    assert ids(filled_bank.search_accounts(owner_name="MILL")) == ["R-USD-1", "R-USD-2"]
    assert ids(filled_bank.search_accounts(min_balance=4_000, max_balance=5_000)) == [
        "J-USD-2",
        "M-USD-1",
    ]
    assert ids(filled_bank.search_accounts(client_id="C-1", currency="USD", min_balance=2_000)) == [
        "J-USD-2"
    ]
    assert len(filled_bank.search_accounts()) == 6


def test_search_accounts_rejects_bad_filters(filled_bank):
    with pytest.raises(ClientNotFoundError):
        filled_bank.search_accounts(client_id="NOPE")
    with pytest.raises(InvalidOperationError):
        filled_bank.search_accounts(status="deleted")
    with pytest.raises(InvalidOperationError):
        filled_bank.search_accounts(currency="GBP")


def test_total_balance_per_currency(filled_bank):
    # 1 000 + 4 000 + 10 000 (4 000 cash + 6 000 invested) + 10 000
    assert filled_bank.get_total_balance() == Decimal("25000.00")
    assert filled_bank.get_total_balance("EUR") == Decimal("700.00")
    assert filled_bank.get_total_balance(Currency.KZT) == Decimal("0.00")


def test_clients_ranking(filled_bank):
    ranking = filled_bank.get_clients_ranking()
    assert [(r.position, r.client.full_name, r.total) for r in ranking] == [
        (1, "Mary Williams", Decimal("10000.00")),
        (2, "Robert Miller", Decimal("10000.00")),
        (3, "John Smith", Decimal("5000.00")),
    ]
    assert [r.client.client_id for r in filled_bank.get_clients_ranking("EUR")] == ["C-1"]
