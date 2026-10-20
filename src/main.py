"""Demo of BankAccount: creation, status checks and money operations."""

from typing import Any, Callable

from exceptions import AccountError
from models import AccountStatus, BankAccount, Owner


def attempt(description: str, operation: Callable[[], Any]) -> None:
    """Run an operation and print its result or the caught error."""
    try:
        result = operation()
        print(f"  [OK]   {description} -> {result}")
    except AccountError as error:
        print(f"  [FAIL] {description} -> {type(error).__name__}: {error}")


def main() -> None:
    print("=== Creating accounts ===")
    active = BankAccount(Owner("Ivan Petrov", "ivan@example.com"), balance=1000)
    frozen = BankAccount(
        Owner("Anna Smirnova"),
        account_id="40817810099910004312",
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
    attempt("Currency GBP", lambda: BankAccount(Owner("Oleg"), currency="GBP"))
    attempt("Empty owner name", lambda: BankAccount(Owner("  ")))
    attempt("Negative balance", lambda: BankAccount(Owner("Oleg"), balance=-5))

    print("\n=== Status lifecycle ===")
    attempt("Unfreeze", frozen.unfreeze)
    attempt("Withdraw 250.50", lambda: frozen.withdraw("250.50"))
    attempt("Close", frozen.close)
    attempt("Deposit to closed account", lambda: frozen.deposit(10))
    print(frozen)

    print("\n=== get_account_info() ===")
    print(active.get_account_info())


if __name__ == "__main__":
    main()
