"""Demo of all account types, the bank, transactions, and the audit and risk analysis."""

import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from accounts import InvestmentAccount, PremiumAccount, SavingsAccount
from audit import AuditLog
from bank import Bank
from clients import Client
from exceptions import BankError
from models import AccountStatus, BankAccount, Owner
from reports import AuditReport
from risk import RiskAnalyzer
from transactions import ExchangeRates, Transaction, TransactionProcessor, TransactionQueue

# Illustrative yearly rates for the training model, not real market data
DEMO_GROWTH_RATES = {"stocks": 0.10, "bonds": 0.04, "etf": 0.07}
# Illustrative exchange rates: the value of one unit in USD
DEMO_EXCHANGE_RATES = {"EUR": "1.08", "RUB": "0.011", "KZT": "0.002", "CNY": "0.14"}


def attempt(description: str, operation: Callable[[], Any]) -> None:
    """Run an operation and print its result or the caught error."""
    try:
        result = operation()
        print(f"  [OK]   {description} -> {result}")
    except BankError as error:
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


class DemoClock:
    """A settable clock, so the demo shows day and night rules whenever it is run."""

    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def demo_bank() -> None:
    clock = DemoClock(datetime(2026, 10, 21, 14, 0))
    bank = Bank("Training Bank", clock=clock)

    print("\n=== Bank: clients ===")
    john = bank.add_client(
        Client(
            "John Smith",
            date(1985, 3, 12),
            "1234",
            client_id="CL-001",
            phone="+1 (555) 010-2030",
            email="john@example.com",
        )
    )
    mary = bank.add_client(
        Client(
            "Mary Williams", date(1992, 7, 30), "5678", client_id="CL-002", email="mary@example.com"
        )
    )
    robert = bank.add_client(Client("Robert Miller", date(1978, 1, 5), "9012", client_id="CL-003"))
    for client in bank.clients:
        print(f"  {client} | {client.contacts}")
    attempt("Add a 16-year-old", lambda: Client("Tim Young", date(2010, 5, 1), "1111"))
    attempt(
        "Add a client with phone '123'",
        lambda: Client("Ann Lee", date(1990, 1, 1), "1111", phone="123"),
    )
    attempt(
        "Add CL-001 again",
        lambda: bank.add_client(Client("Copy", date(1990, 1, 1), "1111", client_id="CL-001")),
    )

    print("\n=== Bank: opening accounts ===")
    open_account = bank.open_account
    attempt(
        "John: bank account",
        lambda: open_account(john.client_id, account_id="JOHN-USD-1", balance=5_000),
    )
    attempt(
        "John: savings account",
        lambda: open_account(
            john.client_id,
            SavingsAccount,
            account_id="JOHN-SAV-1",
            balance=20_000,
            min_balance=5_000,
        ),
    )
    attempt(
        "John: EUR account",
        lambda: open_account(
            john.client_id, account_id="JOHN-EUR-1", balance=3_000, currency="EUR"
        ),
    )
    attempt(
        "Mary: premium account",
        lambda: open_account(
            mary.client_id, PremiumAccount, account_id="MARY-PRM-1", balance=2_000_000
        ),
    )
    attempt(
        "Robert: investment account",
        lambda: open_account(
            robert.client_id, InvestmentAccount, account_id="ROB-INV-1", balance=60_000
        ),
    )
    attempt("Unknown client", lambda: open_account("CL-999"))
    attempt(
        "Robert: invest 40 000 in etf", lambda: bank.get_account("ROB-INV-1").invest("etf", 40_000)
    )
    print(f"  John's accounts: {john.account_ids}")

    print("\n=== Bank: logins ===")
    login = bank.authenticate_client
    attempt("John, right PIN", lambda: login(john.client_id, "1234"))
    for number in range(1, 4):
        attempt(f"Mary, wrong PIN #{number}", lambda: login(mary.client_id, "0000"))
    attempt("Mary, right PIN after blocking", lambda: login(mary.client_id, "5678"))
    attempt("Mary: withdraw while blocked", lambda: bank.withdraw("MARY-PRM-1", 100))
    attempt("Unknown client ID", lambda: login("CL-404", "1234"))
    attempt("Unblock Mary", lambda: bank.unblock_client(mary.client_id))
    attempt("Mary, right PIN", lambda: login(mary.client_id, "5678"))

    print("\n=== Bank: freezing ===")
    attempt("Freeze JOHN-USD-1", lambda: bank.freeze_account("JOHN-USD-1"))
    attempt("Deposit to the frozen account", lambda: bank.deposit("JOHN-USD-1", 100))
    attempt("Freeze it again", lambda: bank.freeze_account("JOHN-USD-1"))
    attempt("Unfreeze JOHN-USD-1", lambda: bank.unfreeze_account("JOHN-USD-1"))
    attempt("Deposit 100", lambda: bank.deposit("JOHN-USD-1", 100))

    print("\n=== Bank: large operations and night time ===")
    attempt("Mary: withdraw 1 000 000 (flagged)", lambda: bank.withdraw("MARY-PRM-1", 1_000_000))
    clock.now = datetime(2026, 10, 22, 2, 30)
    print(f"  Clock set to {clock.now:%H:%M}")
    attempt("John: withdraw 100 at night", lambda: bank.withdraw("JOHN-USD-1", 100))
    attempt("John: open an account at night", lambda: bank.open_account(john.client_id))
    attempt("John: freeze at night (protective)", lambda: bank.freeze_account("JOHN-EUR-1"))
    clock.now = datetime(2026, 10, 22, 9, 0)
    print(f"  Clock set to {clock.now:%H:%M}")
    attempt("John: unfreeze JOHN-EUR-1", lambda: bank.unfreeze_account("JOHN-EUR-1"))

    print("\n=== Bank: search ===")
    for description, filters in (
        ("John's accounts", {"client_id": john.client_id}),
        ("EUR accounts", {"currency": "EUR"}),
        ("Balance from 10 000 to 100 000", {"min_balance": 10_000, "max_balance": 100_000}),
        ("Savings accounts", {"account_type": SavingsAccount}),
    ):
        found = [a.account_id for a in bank.search_accounts(**filters)]
        print(f"  {description}: {found}")

    print("\n=== Bank: reports ===")
    for currency in ("USD", "EUR"):
        print(f"  Total balance {currency}: {bank.get_total_balance(currency)}")
    print("  Clients ranking (USD):")
    for rank in bank.get_clients_ranking():
        print(f"    {rank.position}. {rank.client.full_name}: {rank.total}")
    print("  Suspicious activity:")
    for activity in bank.get_suspicious_activities():
        print(f"    {activity.timestamp:%Y-%m-%d %H:%M} | {activity.client_id} | {activity.reason}")
    print(f"  {bank}")


def demo_transactions() -> None:
    print("\n=== Transactions: ten transactions in the queue ===")
    clock = DemoClock(datetime(2026, 10, 22, 14, 0))
    bank = Bank("Training Bank", clock=clock)
    john = bank.add_client(Client("John Smith", date(1985, 3, 12), "1234", client_id="CL-001"))
    mary = bank.add_client(Client("Mary Williams", date(1992, 7, 30), "5678", client_id="CL-002"))
    bank.open_account(john.client_id, account_id="JOHN-1001", balance=5_000)
    bank.open_account(john.client_id, account_id="JOHN-1002", balance=1_000, currency="EUR")
    bank.open_account(
        john.client_id, SavingsAccount, account_id="JOHN-1003", balance=2_000, min_balance=500
    )
    bank.open_account(mary.client_id, PremiumAccount, account_id="MARY-2001", balance=1_000, fee=0)
    bank.open_account(mary.client_id, account_id="MARY-2002", balance=100)
    bank.freeze_account("JOHN-1003")

    queue = TransactionQueue(clock=clock)
    processor = TransactionProcessor(bank, rates=ExchangeRates(DEMO_EXCHANGE_RATES))
    night = datetime(2026, 10, 23, 4, 30)

    def queue_tx(description: str, *args: Any, **kwargs: Any) -> None:
        tx_id = f"TX-{len(queue) + 1:02}"
        queue.add(Transaction(*args, transaction_id=tx_id, created_at=clock.now, **kwargs))
        print(f"  Queued {tx_id}: {description}")

    queue_tx("Deposit 1 000 USD to John", "deposit", 1_000, receiver_id="JOHN-1001")
    queue_tx(
        "John: transfer 500 USD to Mary",
        "transfer",
        500,
        sender_id="JOHN-1001",
        receiver_id="MARY-2002",
    )
    queue_tx(
        "John: transfer 200 EUR to his USD account",
        "transfer",
        200,
        "EUR",
        sender_id="JOHN-1002",
        receiver_id="JOHN-1001",
    )
    queue_tx(
        "John: external transfer 1 000 USD (1% fee)",
        "external_transfer",
        1_000,
        sender_id="JOHN-1001",
        receiver_id="DE89370400440532",
    )
    queue_tx(
        "Mary, premium: transfer 3 000 USD into the minus",
        "transfer",
        3_000,
        sender_id="MARY-2001",
        receiver_id="JOHN-1001",
    )
    queue_tx(
        "Mary, regular: transfer 5 000 USD into the minus",
        "transfer",
        5_000,
        sender_id="MARY-2002",
        receiver_id="JOHN-1001",
    )
    queue_tx("Deposit 50 USD to a frozen account", "deposit", 50, receiver_id="JOHN-1003")
    queue_tx(
        "John: urgent withdrawal (priority 10)",
        "withdrawal",
        100,
        sender_id="JOHN-1001",
        priority=10,
    )
    queue_tx(
        "Deferred deposit to Mary at 04:30 tomorrow",
        "deposit",
        300,
        receiver_id="MARY-2002",
        scheduled_at=night,
    )
    queue_tx("Deposit to be cancelled", "deposit", 999, receiver_id="JOHN-1001")

    print("\n=== Transactions: cancel and run the queue ===")
    attempt("Cancel TX-10", lambda: queue.cancel("TX-10", "Cancelled by the client"))
    attempt("Cancel TX-99", lambda: queue.cancel("TX-99"))
    for when in (clock.now, night, datetime(2026, 10, 23, 5, 0)):
        clock.now = when
        processed = processor.run(queue)
        print(f"  Run at {when:%Y-%m-%d %H:%M}: processed {len(processed)}, left {len(queue)}")
        for tx in processed:
            print(f"    {tx}")

    print("\n=== Transactions: results ===")
    for account in bank.search_accounts():
        print(f"  {account}")
    print("  Error log:")
    for error in processor.error_log:
        retry = "retry" if error.will_retry else "final"
        print(
            f"    {error.timestamp:%H:%M} | {error.transaction_id} | attempt {error.attempt} | "
            f"{retry} | {error.error}: {error.message}"
        )


def demo_audit(log_path: Path) -> None:
    print("\n=== Audit: ordinary and suspicious transactions ===")
    clock = DemoClock(datetime(2026, 10, 22, 14, 0))
    audit_log = AuditLog(log_path, clock=clock)
    bank = Bank("Training Bank", clock=clock, audit_log=audit_log)
    john = bank.add_client(Client("John Smith", date(1985, 3, 12), "1234", client_id="CL-001"))
    mary = bank.add_client(Client("Mary Williams", date(1992, 7, 30), "5678", client_id="CL-002"))
    bank.open_account(john.client_id, account_id="JOHN-1001", balance=500_000)
    bank.open_account(mary.client_id, account_id="MARY-2001", balance=5_000)

    rates = ExchangeRates(DEMO_EXCHANGE_RATES)
    analyzer = RiskAnalyzer(bank, rates=rates)
    processor = TransactionProcessor(bank, rates=rates, audit_log=audit_log, risk_analyzer=analyzer)
    queue = TransactionQueue(clock=clock)
    print(f"  Risk thresholds: {analyzer.describe_thresholds()}")

    def queue_tx(description: str, created: datetime, *args: Any, **kwargs: Any) -> None:
        tx_id = f"AU-{len(queue) + 1:02}"
        queue.add(Transaction(*args, transaction_id=tx_id, created_at=created, **kwargs))
        print(f"  Queued {tx_id} ({created:%d.%m %H:%M}): {description}")

    def today(hour: int, minute: int = 0) -> datetime:
        return datetime(2026, 10, 22, hour, minute)

    queue_tx("John: deposit 500", today(9), "deposit", 500, receiver_id="JOHN-1001")
    queue_tx(
        "John: first transfer to Mary",
        today(10),
        "transfer",
        300,
        sender_id="JOHN-1001",
        receiver_id="MARY-2001",
    )
    queue_tx(
        "John: another transfer to Mary",
        today(11),
        "transfer",
        200,
        sender_id="JOHN-1001",
        receiver_id="MARY-2001",
    )
    queue_tx(
        "Mary: withdrawal 9 000, too much", today(12), "withdrawal", 9_000, sender_id="MARY-2001"
    )
    queue_tx("John: withdrawal 15 000", today(13), "withdrawal", 15_000, sender_id="JOHN-1001")
    queue_tx(
        "John: transfer 120 000 to Mary",
        today(13, 5),
        "transfer",
        120_000,
        sender_id="JOHN-1001",
        receiver_id="MARY-2001",
    )
    queue_tx(
        "John: external 10 000 at night to a new account",
        today(2, 30),
        "external_transfer",
        10_000,
        sender_id="JOHN-1001",
        receiver_id="FR7630006000",
    )
    for minute in (50, 52, 54):
        queue_tx("Mary: deposit 100", today(13, minute), "deposit", 100, receiver_id="MARY-2001")
    queue_tx(
        "Mary: transfer 1 000 to John right after",
        today(13, 56),
        "transfer",
        1_000,
        sender_id="MARY-2001",
        receiver_id="JOHN-1001",
    )
    queue_tx(
        "Mary: external 11 000 to a new account",
        today(13, 58),
        "external_transfer",
        11_000,
        sender_id="MARY-2001",
        receiver_id="GB29NWBK6016",
    )

    print("\n=== Audit: processing with risk checks ===")
    for tx in processor.run(queue):
        print(f"  {tx}")

    print("\n=== Audit: log entries of warning level and above ===")
    for entry in audit_log.filter(min_level="warning"):
        print(f"  {entry}")

    print("\n=== Audit: reports ===")
    report = AuditReport(audit_log, analyzer)
    for line in report.render().splitlines():
        print(f"  {line}")

    saved = AuditLog.load(log_path)
    print(f"\n  Audit log file: {len(saved)} entries read back from {log_path.name}")


def main() -> None:
    demo_bank_account()
    accounts: list[BankAccount] = [*demo_savings(), *demo_premium(), *demo_investment()]
    demo_polymorphism(accounts)
    demo_bank()
    demo_transactions()
    # The demo log goes to a temporary folder, so running the demo leaves no files behind
    with tempfile.TemporaryDirectory() as folder:
        demo_audit(Path(folder) / "audit.jsonl")


if __name__ == "__main__":
    main()
