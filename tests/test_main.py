import sys
from decimal import Decimal
from pathlib import Path

import pytest

# Make modules from src/ importable when running pytest from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from exceptions import (  # noqa: E402
    AccountClosedError,
    AccountFrozenError,
    InsufficientFundsError,
    InvalidOperationError,
)
from models import AbstractAccount, AccountStatus, BankAccount, Currency, Owner  # noqa: E402
from utils import format_money, mask_account_id, to_money  # noqa: E402


@pytest.fixture
def owner() -> Owner:
    return Owner("Ivan Petrov", "ivan@example.com")


@pytest.fixture
def account(owner: Owner) -> BankAccount:
    return BankAccount(owner, account_id="ACC-0001", balance=1000)


@pytest.fixture
def frozen_account(owner: Owner) -> BankAccount:
    return BankAccount(owner, account_id="ACC-0002", balance=500, status=AccountStatus.FROZEN)


# --- Abstract class ---


def test_abstract_account_cannot_be_instantiated(owner):
    with pytest.raises(TypeError):
        AbstractAccount(owner, "ACC-0001")


def test_balance_is_read_only(account):
    with pytest.raises(AttributeError):
        account.balance = Decimal("1000000")


# --- Creation and validation ---


def test_generates_short_id_when_missing(owner):
    acc = BankAccount(owner)
    assert len(acc.account_id) == 12
    assert acc.account_id.isalnum()


def test_uses_injected_id_generator(owner):
    acc = BankAccount(owner, id_generator=lambda: "FIXED-9999")
    assert acc.account_id == "FIXED-9999"


def test_generated_ids_are_unique(owner):
    assert len({BankAccount(owner).account_id for _ in range(100)}) == 100


@pytest.mark.parametrize("bad_id", ["", "   ", "123", 12345])
def test_rejects_invalid_account_id(owner, bad_id):
    with pytest.raises(InvalidOperationError):
        BankAccount(owner, account_id=bad_id)


@pytest.mark.parametrize("code", ["RUB", "usd", "Eur", "KZT", Currency.CNY])
def test_accepts_supported_currencies(owner, code):
    assert BankAccount(owner, currency=code).currency in Currency


@pytest.mark.parametrize("code", ["GBP", "", "rubles"])
def test_rejects_unsupported_currency(owner, code):
    with pytest.raises(InvalidOperationError):
        BankAccount(owner, currency=code)


def test_rejects_negative_initial_balance(owner):
    with pytest.raises(InvalidOperationError):
        BankAccount(owner, balance=-1)


def test_rejects_unknown_status(owner):
    with pytest.raises(InvalidOperationError):
        BankAccount(owner, status="active")


def test_rejects_non_owner(owner):
    with pytest.raises(InvalidOperationError):
        BankAccount("Ivan Petrov")


@pytest.mark.parametrize("name", ["", "   "])
def test_owner_requires_name(name):
    with pytest.raises(InvalidOperationError):
        Owner(name)


def test_owner_rejects_bad_email():
    with pytest.raises(InvalidOperationError):
        Owner("Ivan", "not-an-email")


# --- Money operations ---


def test_valid_deposit_and_withdraw(account):
    assert account.deposit("500.25") == Decimal("1500.25")
    assert account.withdraw(300) == Decimal("1200.25")
    assert account.balance == Decimal("1200.25")


def test_float_amounts_have_no_rounding_errors(account):
    account.deposit(0.1)
    account.deposit(0.2)
    assert account.balance == Decimal("1000.30")


def test_withdraw_whole_balance(account):
    assert account.withdraw(1000) == Decimal("0.00")


@pytest.mark.parametrize("amount", [0, -10, "abc", None, True, float("nan"), float("inf"), 1.005])
def test_rejects_invalid_amounts(account, amount):
    with pytest.raises(InvalidOperationError):
        account.deposit(amount)
    with pytest.raises(InvalidOperationError):
        account.withdraw(amount)
    assert account.balance == Decimal("1000.00")


def test_insufficient_funds(account):
    with pytest.raises(InsufficientFundsError) as exc_info:
        account.withdraw("1000.01")
    assert exc_info.value.requested == Decimal("1000.01")
    assert account.balance == Decimal("1000.00")


# --- Statuses ---


def test_frozen_account_blocks_operations(frozen_account):
    with pytest.raises(AccountFrozenError):
        frozen_account.deposit(100)
    with pytest.raises(AccountFrozenError):
        frozen_account.withdraw(100)
    assert frozen_account.balance == Decimal("500.00")


def test_closed_account_blocks_operations(owner):
    acc = BankAccount(owner, status=AccountStatus.CLOSED)
    with pytest.raises(AccountClosedError):
        acc.deposit(100)
    with pytest.raises(AccountClosedError):
        acc.withdraw(100)


def test_freeze_and_unfreeze(account):
    account.freeze()
    assert account.status is AccountStatus.FROZEN
    account.unfreeze()
    assert account.status is AccountStatus.ACTIVE
    account.deposit(1)


def test_cannot_freeze_twice(account):
    account.freeze()
    with pytest.raises(InvalidOperationError):
        account.freeze()


def test_close_requires_zero_balance(account):
    with pytest.raises(InvalidOperationError):
        account.close()
    account.withdraw(1000)
    account.close()
    assert account.status is AccountStatus.CLOSED


def test_closed_status_is_terminal(owner):
    acc = BankAccount(owner, status=AccountStatus.CLOSED)
    for action in (acc.freeze, acc.unfreeze, acc.close):
        with pytest.raises(AccountClosedError):
            action()


# --- String representation and info ---


def test_str_contains_required_parts(account):
    text = str(account)
    assert "Bank account" in text
    assert "Ivan Petrov" in text
    assert "****0001" in text
    assert "ACC-0001" not in text
    assert "active" in text
    assert "1 000.00 RUB" in text


def test_get_account_info(account):
    assert account.get_account_info() == {
        "type": "Bank account",
        "account_id": "ACC-0001",
        "owner": "Ivan Petrov",
        "email": "ivan@example.com",
        "status": "active",
        "balance": Decimal("1000.00"),
        "currency": "RUB",
    }


# --- Utils ---


def test_to_money_quantizes_to_cents():
    assert to_money(5) == Decimal("5.00")
    assert to_money("0", allow_zero=True) == Decimal("0.00")


def test_mask_and_format_helpers():
    assert mask_account_id("40817810099910004312") == "****4312"
    assert format_money(Decimal("1234567.5")) == "1 234 567.50"
