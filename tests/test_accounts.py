import sys
from decimal import Decimal
from pathlib import Path

import pytest

# Make modules from src/ importable when running pytest from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from accounts import (  # noqa: E402
    AssetType,
    InvestmentAccount,
    PremiumAccount,
    SavingsAccount,
)
from exceptions import (  # noqa: E402
    AccountFrozenError,
    InsufficientFundsError,
    InvalidOperationError,
    LimitExceededError,
)
from models import AccountStatus, BankAccount, Owner  # noqa: E402

DEMO_RATES = {"stocks": "0.10", "bonds": "0.04", "etf": "0.07"}


@pytest.fixture
def owner() -> Owner:
    return Owner("John Smith", "john@example.com")


@pytest.fixture
def savings(owner: Owner) -> SavingsAccount:
    return SavingsAccount(
        owner, account_id="SAV-0001", balance=10_000, min_balance=1_000, monthly_rate="0.015"
    )


@pytest.fixture
def premium(owner: Owner) -> PremiumAccount:
    return PremiumAccount(
        owner,
        account_id="PRM-0001",
        balance=1_000,
        withdrawal_limit=50_000,
        overdraft_limit=5_000,
        fee=25,
    )


@pytest.fixture
def investment(owner: Owner) -> InvestmentAccount:
    return InvestmentAccount(owner, account_id="INV-0001", balance=100_000)


# --- SavingsAccount ---


def test_savings_withdraw_keeps_min_balance(savings):
    assert savings.withdraw(9_000) == Decimal("1000.00")
    with pytest.raises(InsufficientFundsError) as exc_info:
        savings.withdraw("0.01")
    assert exc_info.value.available == Decimal("0.00")
    assert savings.balance == Decimal("1000.00")


def test_savings_allows_withdrawing_whole_balance(savings):
    assert savings.withdraw(10_000) == Decimal("0.00")
    savings.close()
    assert savings.status is AccountStatus.CLOSED


def test_savings_monthly_interest(savings):
    assert savings.apply_monthly_interest() == Decimal("150.00")
    assert savings.balance == Decimal("10150.00")
    # Compounds on the next month
    assert savings.apply_monthly_interest() == Decimal("152.25")


def test_savings_interest_is_rounded_to_cents(owner):
    acc = SavingsAccount(owner, balance="333.33", monthly_rate="0.01")
    assert acc.apply_monthly_interest() == Decimal("3.33")


def test_savings_interest_blocked_when_frozen(savings):
    savings.freeze()
    with pytest.raises(AccountFrozenError):
        savings.apply_monthly_interest()
    assert savings.balance == Decimal("10000.00")


def test_savings_rejects_balance_below_minimum(owner):
    with pytest.raises(InvalidOperationError):
        SavingsAccount(owner, balance=500, min_balance=1_000)


@pytest.mark.parametrize("rate", [-0.01, 1.5, "abc", True, float("nan")])
def test_savings_rejects_invalid_rate(owner, rate):
    with pytest.raises(InvalidOperationError):
        SavingsAccount(owner, monthly_rate=rate)


# --- PremiumAccount ---


def test_premium_charges_fixed_fee(premium):
    assert premium.withdraw(100) == Decimal("875.00")


def test_premium_overdraft(premium):
    # 1 000 balance + 5 000 overdraft = 6 000 available, fee included
    assert premium.withdraw(5_975) == Decimal("-5000.00")
    with pytest.raises(InsufficientFundsError):
        premium.withdraw(1)
    premium.deposit(5_000)
    assert premium.balance == Decimal("0.00")


def test_premium_overdraft_counts_fee(premium):
    with pytest.raises(InsufficientFundsError) as exc_info:
        premium.withdraw(5_976)
    assert exc_info.value.requested == Decimal("6001.00")
    assert premium.balance == Decimal("1000.00")


def test_premium_withdrawal_limit(owner):
    acc = PremiumAccount(owner, balance=200_000, withdrawal_limit=100_000)
    with pytest.raises(LimitExceededError):
        acc.withdraw("100000.01")
    acc.withdraw(100_000)


def test_premium_cannot_close_in_overdraft(premium):
    premium.withdraw(2_000)
    with pytest.raises(InvalidOperationError):
        premium.close()


# --- InvestmentAccount ---


def test_invest_moves_cash_into_portfolio(investment):
    assert investment.invest("stocks", 50_000) == Decimal("50000.00")
    assert investment.invest(AssetType.BONDS, 20_000) == Decimal("20000.00")
    assert investment.balance == Decimal("30000.00")
    assert investment.invested_amount == Decimal("70000.00")
    assert investment.total_value == Decimal("100000.00")


def test_invest_checks(investment):
    with pytest.raises(InvalidOperationError):
        investment.invest("crypto", 100)
    with pytest.raises(InvalidOperationError):
        investment.invest("etf", -100)
    with pytest.raises(InsufficientFundsError):
        investment.invest("etf", 100_001)
    assert investment.invested_amount == Decimal("0.00")
    assert investment.balance == Decimal("100000.00")


def test_portfolio_is_read_only_copy(investment):
    investment.invest("etf", 100)
    investment.portfolio[AssetType.ETF] = Decimal("999999")
    assert investment.portfolio[AssetType.ETF] == Decimal("100.00")


def test_divest(investment):
    investment.invest("etf", 10_000)
    assert investment.divest("etf", 4_000) == Decimal("94000.00")
    with pytest.raises(InsufficientFundsError):
        investment.divest("etf", 6_001)


def test_withdraw_only_free_cash(investment):
    investment.invest("stocks", 90_000)
    with pytest.raises(InsufficientFundsError):
        investment.withdraw(10_001)
    assert investment.withdraw(10_000) == Decimal("0.00")


def test_project_yearly_growth(investment):
    investment.invest("stocks", 50_000)
    investment.invest("bonds", 30_000)
    investment.invest("etf", 20_000)
    projection = investment.project_yearly_growth(DEMO_RATES)
    assert projection.by_asset == {
        AssetType.STOCKS: Decimal("5000.00"),
        AssetType.BONDS: Decimal("1200.00"),
        AssetType.ETF: Decimal("1400.00"),
    }
    assert projection.total_growth == Decimal("7600.00")
    assert projection.projected_value == Decimal("107600.00")
    # A projection does not change the account
    assert investment.invested_amount == Decimal("100000.00")


def test_growth_rate_only_needed_for_held_assets(investment):
    investment.invest("bonds", 1_000)
    projection = investment.project_yearly_growth({"bonds": 0.04})
    assert projection.total_growth == Decimal("40.00")


def test_growth_allows_negative_rate(investment):
    investment.invest("stocks", 1_000)
    assert investment.project_yearly_growth({"stocks": "-0.2"}).total_growth == Decimal("-200.00")


@pytest.mark.parametrize(
    "rates",
    [
        {"stocks": 0.1},  # bonds has money but no rate
        {"stocks": 0.1, "bonds": 0.04, "gold": 0.05},  # unknown asset type
        {"stocks": 0.1, "bonds": -1.5},  # loses more than everything
        {"stocks": 0.1, "bonds": "abc"},
        {"stocks": 0.1, "STOCKS": 0.2, "bonds": 0.04},  # same asset twice
    ],
)
def test_project_yearly_growth_rejects_bad_rates(investment, rates):
    investment.invest("stocks", 1_000)
    investment.invest("bonds", 1_000)
    with pytest.raises(InvalidOperationError):
        investment.project_yearly_growth(rates)


def test_cannot_close_with_invested_money(investment):
    investment.invest("etf", 100_000)
    with pytest.raises(InvalidOperationError):
        investment.close()
    investment.divest("etf", 100_000)
    investment.withdraw(100_000)
    investment.close()


def test_frozen_investment_blocks_operations(investment):
    investment.freeze()
    with pytest.raises(AccountFrozenError):
        investment.invest("etf", 100)


# --- Polymorphism ---


def test_every_type_is_a_bank_account(savings, premium, investment):
    for acc in (savings, premium, investment):
        assert isinstance(acc, BankAccount)
        acc.deposit(100)
        acc.withdraw(100)


def test_account_info_extends_base_fields(savings, premium, investment):
    base_keys = {"type", "account_id", "owner", "email", "status", "balance", "currency"}
    assert savings.get_account_info().keys() >= base_keys | {"min_balance", "monthly_rate"}
    assert premium.get_account_info().keys() >= base_keys | {"overdraft_limit", "fee"}
    info = investment.get_account_info()
    assert info.keys() >= base_keys | {"portfolio", "invested_amount", "total_value"}
    assert info["type"] == "Investment account"


def test_str_per_type(savings, premium, investment):
    investment.invest("etf", 1_000)
    assert "Savings account" in str(savings) and "1.50%/month" in str(savings)
    assert "Premium account" in str(premium) and "Fee: 25.00" in str(premium)
    assert "Investment account" in str(investment) and "etf 1 000.00" in str(investment)
    assert "SAV-0001" not in str(savings)
