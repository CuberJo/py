"""Advanced account types built on BankAccount: savings, premium and investment."""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Union

from exceptions import InsufficientFundsError, InvalidOperationError, LimitExceededError
from models import BankAccount, Owner
from utils import (
    Amount,
    Rate,
    format_money,
    format_percent,
    round_money,
    to_money,
    to_rate,
)


class SavingsAccount(BankAccount):
    """An account that keeps a minimum balance and earns monthly interest.

    A withdrawal must leave at least ``min_balance`` on the account, or take
    the whole balance at once (closing the savings out).
    """

    account_type = "Savings account"

    DEFAULT_MONTHLY_RATE = Decimal("0.01")

    def __init__(
        self,
        owner: Owner,
        *args: Any,
        min_balance: Amount = 0,
        monthly_rate: Rate = DEFAULT_MONTHLY_RATE,
        **kwargs: Any,
    ):
        # Other arguments are the same as in BankAccount
        super().__init__(owner, *args, **kwargs)
        self._min_balance = to_money(min_balance, allow_zero=True)
        self._monthly_rate = to_rate(monthly_rate, maximum=Decimal(1))
        if self._balance < self._min_balance:
            raise InvalidOperationError(
                f"Initial balance {format_money(self._balance)} is below the minimum "
                f"balance {format_money(self._min_balance)}"
            )

    @property
    def min_balance(self) -> Decimal:
        return self._min_balance

    @property
    def monthly_rate(self) -> Decimal:
        return self._monthly_rate

    @property
    def available_funds(self) -> Decimal:
        """How much can be withdrawn while keeping the minimum balance."""
        return max(self._balance - self._min_balance, Decimal("0.00"))

    def withdraw(self, amount: Amount) -> Decimal:
        self._ensure_operable()
        amount = to_money(amount)
        if amount == self._balance:
            return self._debit(amount, available=self._balance)
        return self._debit(amount, available=self.available_funds)

    def apply_monthly_interest(self) -> Decimal:
        """Credit one month of interest and return the credited amount."""
        self._ensure_operable()
        interest = round_money(self._balance * self._monthly_rate)
        self._balance += interest
        return interest

    def get_account_info(self) -> dict[str, Any]:
        info = super().get_account_info()
        info.update(
            min_balance=self._min_balance,
            monthly_rate=self._monthly_rate,
            available_funds=self.available_funds,
        )
        return info

    def __str__(self) -> str:
        return (
            f"{super().__str__()} | "
            f"Min. balance: {format_money(self._min_balance)} | "
            f"Rate: {format_percent(self._monthly_rate)}/month"
        )


class PremiumAccount(BankAccount):
    """An account with a high per-operation limit, an overdraft and a fixed fee.

    Every withdrawal costs ``fee`` on top of the amount; the balance may go
    down to ``-overdraft_limit``.
    """

    account_type = "Premium account"

    DEFAULT_WITHDRAWAL_LIMIT = Decimal("1000000.00")
    DEFAULT_OVERDRAFT_LIMIT = Decimal("100000.00")
    DEFAULT_FEE = Decimal("50.00")

    def __init__(
        self,
        owner: Owner,
        *args: Any,
        withdrawal_limit: Amount = DEFAULT_WITHDRAWAL_LIMIT,
        overdraft_limit: Amount = DEFAULT_OVERDRAFT_LIMIT,
        fee: Amount = DEFAULT_FEE,
        **kwargs: Any,
    ):
        # Other arguments are the same as in BankAccount
        super().__init__(owner, *args, **kwargs)
        self._withdrawal_limit = to_money(withdrawal_limit)
        self._overdraft_limit = to_money(overdraft_limit, allow_zero=True)
        self._fee = to_money(fee, allow_zero=True)

    @property
    def withdrawal_limit(self) -> Decimal:
        return self._withdrawal_limit

    @property
    def overdraft_limit(self) -> Decimal:
        return self._overdraft_limit

    @property
    def fee(self) -> Decimal:
        return self._fee

    @property
    def available_funds(self) -> Decimal:
        """Balance plus overdraft: the most one withdrawal (amount + fee) can take."""
        return self._balance + self._overdraft_limit

    def withdraw(self, amount: Amount) -> Decimal:
        self._ensure_operable()
        amount = to_money(amount)
        if amount > self._withdrawal_limit:
            raise LimitExceededError(self._withdrawal_limit, amount, self._currency.value)
        return self._debit(amount + self._fee, available=self.available_funds)

    def get_account_info(self) -> dict[str, Any]:
        info = super().get_account_info()
        info.update(
            withdrawal_limit=self._withdrawal_limit,
            overdraft_limit=self._overdraft_limit,
            fee=self._fee,
            available_funds=self.available_funds,
        )
        return info

    def __str__(self) -> str:
        return (
            f"{super().__str__()} | "
            f"Overdraft: {format_money(self._overdraft_limit)} | "
            f"Fee: {format_money(self._fee)}"
        )


class AssetType(Enum):
    """Virtual asset classes of the training investment model."""

    STOCKS = "stocks"
    BONDS = "bonds"
    ETF = "etf"

    @classmethod
    def parse(cls, value: Union["AssetType", str]) -> "AssetType":
        """Accept an AssetType member or a case-insensitive name such as 'ETF'."""
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).lower())
        except ValueError:
            allowed = ", ".join(a.value for a in cls)
            raise InvalidOperationError(
                f"Unsupported asset type: {value!r}. Allowed: {allowed}"
            ) from None


@dataclass(frozen=True)
class GrowthProjection:
    """Expected portfolio growth over one year."""

    by_asset: dict[AssetType, Decimal]
    total_growth: Decimal
    projected_value: Decimal


class InvestmentAccount(BankAccount):
    """An account with free cash (the balance) and a portfolio of virtual assets.

    ``invest`` moves cash into an asset type, ``divest`` moves it back.
    Only free cash can be withdrawn.
    """

    account_type = "Investment account"

    # Growth rate is a fraction of the invested amount; -1 means losing everything
    MIN_GROWTH_RATE = Decimal(-1)

    def __init__(self, owner: Owner, *args: Any, **kwargs: Any):
        # Arguments are the same as in BankAccount
        super().__init__(owner, *args, **kwargs)
        self._portfolio: dict[AssetType, Decimal] = {
            asset: Decimal("0.00") for asset in AssetType
        }

    @property
    def portfolio(self) -> dict[AssetType, Decimal]:
        # A copy, so the portfolio changes only through invest/divest
        return dict(self._portfolio)

    @property
    def invested_amount(self) -> Decimal:
        return sum(self._portfolio.values(), Decimal("0.00"))

    @property
    def total_value(self) -> Decimal:
        return self._balance + self.invested_amount

    def invest(self, asset_type: Union[AssetType, str], amount: Amount) -> Decimal:
        """Move free cash into an asset type and return the new holding."""
        self._ensure_operable()
        asset = AssetType.parse(asset_type)
        amount = to_money(amount)
        self._debit(amount, available=self._balance)
        self._portfolio[asset] += amount
        return self._portfolio[asset]

    def divest(self, asset_type: Union[AssetType, str], amount: Amount) -> Decimal:
        """Move money from an asset type back to free cash and return the new balance."""
        self._ensure_operable()
        asset = AssetType.parse(asset_type)
        amount = to_money(amount)
        holding = self._portfolio[asset]
        if amount > holding:
            raise InsufficientFundsError(holding, amount, self._currency.value)
        self._portfolio[asset] -= amount
        self._balance += amount
        return self._balance

    def project_yearly_growth(
        self, growth_rates: Mapping[Union[AssetType, str], Rate]
    ) -> GrowthProjection:
        """Estimate one year of growth from yearly rates per asset type.

        ``growth_rates`` maps an asset type to a fractional yearly rate
        (``{"stocks": 0.10}`` means +10%). Every asset type that holds money
        must have a rate.
        """
        rates: dict[AssetType, Decimal] = {}
        for key, value in growth_rates.items():
            asset = AssetType.parse(key)
            if asset in rates:
                raise InvalidOperationError(f"Duplicate growth rate for {asset.value}")
            rates[asset] = to_rate(value, minimum=self.MIN_GROWTH_RATE)

        by_asset: dict[AssetType, Decimal] = {}
        for asset, holding in self._portfolio.items():
            if holding == 0:
                continue
            if asset not in rates:
                raise InvalidOperationError(f"No growth rate given for {asset.value}")
            by_asset[asset] = round_money(holding * rates[asset])

        total_growth = sum(by_asset.values(), Decimal("0.00"))
        return GrowthProjection(
            by_asset=by_asset,
            total_growth=total_growth,
            projected_value=self.invested_amount + total_growth,
        )

    def withdraw(self, amount: Amount) -> Decimal:
        # Invested money is not available until it is divested
        self._ensure_operable()
        amount = to_money(amount)
        return self._debit(amount, available=self._balance)

    def close(self) -> None:
        if self.invested_amount != 0:
            raise InvalidOperationError(
                f"Cannot close an account with invested money "
                f"({format_money(self.invested_amount)} {self._currency.value})"
            )
        super().close()

    def get_account_info(self) -> dict[str, Any]:
        info = super().get_account_info()
        info.update(
            portfolio={asset.value: amount for asset, amount in self._portfolio.items()},
            invested_amount=self.invested_amount,
            total_value=self.total_value,
        )
        return info

    def __str__(self) -> str:
        holdings = ", ".join(
            f"{asset.value} {format_money(amount)}"
            for asset, amount in self._portfolio.items()
            if amount
        )
        return f"{super().__str__()} | Portfolio: {holdings or 'empty'}"
