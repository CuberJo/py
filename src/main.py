"""Demo of all account types: creation, status checks and money operations."""

from typing import Any, Callable

from accounts import InvestmentAccount, PremiumAccount, SavingsAccount
from exceptions import AccountError
from models import AccountStatus, BankAccount, Owner

# Illustrative yearly rates for the training model, not real market data
DEMO_GROWTH_RATES = {"stocks": 0.10, "bonds": 0.04, "etf": 0.07}


def attempt(description: str, operation: Callable[[], Any]) -> None:
    """Run an operation and print its result or the caught error."""
    try:
        result = operation()
        print(f"  [OK]   {description} -> {result}")
    except AccountError as error:
        print(f"  [FAIL] {description} -> {type(error).__name__}: {error}")


def demo_bank_account() -> None:
    print("=== Creating accounts ===")
    active = BankAccount(Owner("John Smith", "john@example.com"), balance=1000)
    frozen = BankAccount(
        Owner("Emily Johnson"),
        account_id="987654324312",
        balance="250.50",
        currency="usd",
        status=AccountStatus.FROZEN,
    )
    print(active)
    print(frozen)

    print("\n=== Operations on a frozen account ===")
    attempt("Deposit 100", lambda: frozen.deposit(100))
    attempt("Withdraw 50", lambda: frozen.withdraw(50))

    print("\n=== Valid operations on an active account ===")
    attempt("Deposit 500.25", lambda: active.deposit(500.25))
    attempt("Withdraw 300", lambda: active.withdraw(300))
    print(active)

    print("\n=== Amount checks ===")
    attempt("Deposit -100", lambda: active.deposit(-100))
    attempt("Deposit 0", lambda: active.deposit(0))
    attempt("Deposit 'abc'", lambda: active.deposit("abc"))
    attempt("Deposit 10.001", lambda: active.deposit(10.001))
    attempt("Withdraw 1 000 000", lambda: active.withdraw(1_000_000))

    print("\n=== Constructor checks ===")
    attempt("Currency GBP", lambda: BankAccount(Owner("Mike"), currency="GBP"))
    attempt("Empty owner name", lambda: BankAccount(Owner("  ")))
    attempt("Negative balance", lambda: BankAccount(Owner("Mike"), balance=-5))

    print("\n=== Status lifecycle ===")
    attempt("Unfreeze", frozen.unfreeze)
    attempt("Withdraw 250.50", lambda: frozen.withdraw("250.50"))
    attempt("Close", frozen.close)
    attempt("Deposit to closed account", lambda: frozen.deposit(10))
    print(frozen)

    print("\n=== get_account_info() ===")
    print(active.get_account_info())


def demo_savings() -> list[SavingsAccount]:
    print("\n=== SavingsAccount ===")
    pension = SavingsAccount(
        Owner("Mary Williams"), balance=50_000, min_balance=10_000, monthly_rate="0.012"
    )
    student = SavingsAccount(Owner("Tyler Brown"), balance=2_000, min_balance=500)
    print(pension)
    print(student)
    attempt("Pension: monthly interest", pension.apply_monthly_interest)
    attempt("Pension: withdraw 45 000 (below minimum)", lambda: pension.withdraw(45_000))
    attempt("Pension: withdraw 30 000", lambda: pension.withdraw(30_000))
    attempt("Student: withdraw 1 600 (below minimum)", lambda: student.withdraw(1_600))
    attempt("Student: withdraw whole balance", lambda: student.withdraw(2_000))
    attempt("Student: close", student.close)
    attempt(
        "Balance below minimum at creation",
        lambda: SavingsAccount(Owner("Mike"), balance=100, min_balance=1_000),
    )
    return [pension, student]


def demo_premium() -> list[PremiumAccount]:
    print("\n=== PremiumAccount ===")
    business = PremiumAccount(Owner("Robert Miller"), balance=300_000)
    vip = PremiumAccount(Owner("Jennifer Davis"), balance=1_000, overdraft_limit=20_000, fee=0)
    print(business)
    print(vip)
    attempt("Business: withdraw 250 000 (+ fee 50)", lambda: business.withdraw(250_000))
    attempt("Business: withdraw 1 500 000 (over limit)", lambda: business.withdraw(1_500_000))
    attempt("VIP: withdraw 15 000 (uses overdraft)", lambda: vip.withdraw(15_000))
    attempt("VIP: withdraw 10 000 (overdraft exhausted)", lambda: vip.withdraw(10_000))
    attempt("VIP: close with a debt", vip.close)
    attempt("VIP: deposit 14 000", lambda: vip.deposit(14_000))
    return [business, vip]


def demo_investment() -> list[InvestmentAccount]:
    print("\n=== InvestmentAccount ===")
    growth = InvestmentAccount(Owner("Michael Wilson"), balance=200_000)
    careful = InvestmentAccount(Owner("Linda Moore"), balance=50_000)
    attempt("Growth: invest 100 000 in stocks", lambda: growth.invest("stocks", 100_000))
    attempt("Growth: invest 50 000 in etf", lambda: growth.invest("etf", 50_000))
    attempt("Growth: invest 30 000 in bonds", lambda: growth.invest("bonds", 30_000))
    attempt("Careful: invest 45 000 in bonds", lambda: careful.invest("bonds", 45_000))
    attempt("Careful: invest 10 000 in etf (no cash)", lambda: careful.invest("etf", 10_000))
    attempt("Careful: invest in crypto", lambda: careful.invest("crypto", 100))
    attempt("Careful: invest -5", lambda: careful.invest("etf", -5))
    attempt("Careful: withdraw 10 000 (money is invested)", lambda: careful.withdraw(10_000))
    attempt("Careful: divest 5 000 from bonds", lambda: careful.divest("bonds", 5_000))
    print(growth)
    print(careful)
    for account in (growth, careful):
        projection = account.project_yearly_growth(DEMO_GROWTH_RATES)
        by_asset = ", ".join(
            f"{asset.value} +{amount}" for asset, amount in projection.by_asset.items()
        )
        print(
            f"  {account.owner}: expected yearly growth {projection.total_growth} "
            f"({by_asset}), portfolio after a year {projection.projected_value}"
        )
    return [growth, careful]


def demo_polymorphism(accounts: list[BankAccount]) -> None:
    # The same calls behave differently depending on the account type
    print("\n=== Polymorphism: withdraw 1 000 from every account ===")
    for account in accounts:
        attempt(f"{account.account_type} of {account.owner}", lambda: account.withdraw(1_000))
    print("\n=== Polymorphism: __str__ and get_account_info() ===")
    for account in accounts:
        print(account)
        print(f"  {account.get_account_info()}")


def main() -> None:
    demo_bank_account()
    accounts: list[BankAccount] = [*demo_savings(), *demo_premium(), *demo_investment()]
    demo_polymorphism(accounts)


if __name__ == "__main__":
    main()
