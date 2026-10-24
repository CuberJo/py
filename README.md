# Bank Account Model

A training model of bank accounts for practising OOP.
`AbstractAccount` holds the shared state and rules; `BankAccount` is a concrete
single-currency account with input validation, status checks and auto-generated IDs.
`SavingsAccount`, `PremiumAccount` and `InvestmentAccount` extend `BankAccount`
with their own withdrawal rules and extra features.
`Bank` manages `Client`s and their accounts, with PIN logins and security rules.
`Transaction`, `TransactionQueue` and `TransactionProcessor` run money operations
through a priority queue, with fees, currency conversion and retries.
`AuditLog`, `RiskAnalyzer` and `AuditReport` record every event, score transactions for
signs of fraud, block dangerous ones and report on them.
`TransactionReport` adds bank-wide reports, and the demo ends with a simulation of a whole
banking day. `ReportBuilder` turns the results into client, bank and risk reports
(text, JSON, CSV) and draws charts with matplotlib.

All data lives in memory: there is no database, no external API and no real market data.
Exchange rates are fixed values passed in by the caller.

## Features

- `AbstractAccount` with an account ID, owner, protected (read-only) balance and status
  (`active`, `frozen`, `closed`), plus abstract `deposit`, `withdraw`, `get_account_info`
- `BankAccount` with a currency (`USD` by default; also `RUB`, `EUR`, `KZT`, `CNY`) and a short UUID
  generated when no account number is given
- Custom exceptions: `AccountFrozenError`, `AccountClosedError`,
  `InvalidOperationError`, `InsufficientFundsError`
- Amount checks: numbers only, positive, at most 2 decimal places, no NaN/infinity
- Money stored as `Decimal`, so there are no float rounding errors
- `__str__` shows account type, client, last 4 characters of the number, status, balance and currency

### Account types (Day 2)

| Class | Withdrawal rule | Extra features |
|---|---|---|
| `SavingsAccount` | must leave at least `min_balance`, or take the whole balance at once | monthly interest rate, `apply_monthly_interest()` |
| `PremiumAccount` | up to `withdrawal_limit` per operation (1 000 000 by default); a fixed `fee` is added; the balance may go down to `-overdraft_limit` | high limits, overdraft, fixed fee |
| `InvestmentAccount` | only free cash; invested money must be divested first | portfolio of `stocks`, `bonds`, `etf`; `invest()`, `divest()`, `project_yearly_growth()` |

Every type overrides `withdraw()`, `get_account_info()` (base fields plus its own) and `__str__()`,
so code that works with a `BankAccount` works with any of them.
Rates are fractions: `0.01` is 1%. A failed withdrawal raises `InsufficientFundsError`
(or `LimitExceededError` for a premium amount over the limit) and leaves the account unchanged.

### Bank and clients (Day 3)

- `Client`: full name, ID (generated if not given), status (`active`, `blocked`), birth date
  (must be 18 or older), contacts (phone, email), the numbers of their accounts and a PIN
  of 4–6 digits stored only as a salted hash
- `Bank` methods: `add_client()`, `open_account()` (of any account type), `close_account()`,
  `freeze_account()`, `unfreeze_account()`, `deposit()`, `withdraw()`, `authenticate_client()`,
  `unblock_client()`, `search_accounts()` (by client, owner name, status, currency, type, balance)
- Reports: `get_total_balance(currency)` and `get_clients_ranking(currency)`; invested money counts,
  closed accounts do not, and currencies are never mixed (exchange rates are used only by
  the transaction processor)
- Security:
  - 3 wrong PINs in a row block the client; a blocked client cannot log in or operate until
    `unblock_client()`; a successful login resets the counter
  - no account operations from 00:00 to 05:00 (`OperationTimeError`); freezing is the exception,
    since it protects the client, and logging in is allowed
  - suspicious activity log (`get_suspicious_activities()`): blocking after failed logins, logins
    while blocked or with an unknown ID, night attempts, deposits and withdrawals of 1 000 000 or more

### Transactions (Day 4)

- `Transaction`: ID, type (`deposit`, `withdrawal`, `transfer`, `external_transfer`), amount,
  currency, fee, sender and receiver accounts, status (`pending`, `completed`, `failed`,
  `cancelled`), failure reason, priority, attempts, the debited and credited amounts, and
  timestamps (`created_at`, `scheduled_at`, `processed_at`)
- `TransactionQueue`: `add()`, `cancel()`, `pending()`, `ready()`, `pop_next()`. Higher priority
  goes first, then the order added. A transaction with `scheduled_at` waits until that time
- `TransactionProcessor`: `process()` makes one attempt, `run(queue)` processes everything that
  is due now
  - fees: an external transfer costs 1% of the amount (`external_fee_rate`); internal operations are free
  - conversion: `ExchangeRates` converts the amount to the sender's and the receiver's currency
  - retries: a night-time rejection (`OperationTimeError`) is retried every 30 minutes, at most 3
    attempts in total; any other error fails the transaction at once
  - error log: `error_log` keeps every failed attempt (time, transaction, attempt, error, retry or final)
- Rules, checked before any money moves:
  - only a `PremiumAccount` may go below zero (within its overdraft)
  - a frozen or closed account can neither send nor receive money
  - an external transfer pays the fee on top of the amount
- `Bank.transfer()` moves money between two accounts as one operation: if the receiver
  rejects it, the sender gets the money back

### Audit and risk analysis (Day 5)

- `AuditLog`: every entry has a time, a level (`info`, `warning`, `error`, `critical`), an event
  name, a message, the client, account and transaction involved, and extra details
  - kept in memory and, if a file path is given, appended to a JSON Lines file;
    `AuditLog.load(path)` reads the file back
  - `filter()` by exact level or minimum level, event, client, account, transaction and time range
  - `Bank(audit_log=...)` also writes its suspicious activity (logins, night attempts, large
    operations) to the log as warnings
- `RiskAnalyzer` scores every transaction; each factor adds points:

  | Factor | Rule (defaults) | Points |
  |---|---|---|
  | large amount | 10 000 or more in the base currency (USD) | 2 |
  | very large amount | 100 000 or more | 4 |
  | frequent operations | the client already made 3 operations in the last 10 minutes | 2 |
  | new receiver | a transfer to another client's or an external account the client has never paid | 1 |
  | night | made from 23:00 to 06:00 | 1 |

  The total gives the risk level: 0–1 low, 2–3 medium, 4 or more high.
  Amounts in other currencies are converted with `ExchangeRates`.
- Blocking: `TransactionProcessor(risk_analyzer=..., audit_log=...)` checks the risk before any
  money moves. A high-risk transaction fails with `SuspiciousOperationError` and is not retried
  (`block_level="medium"` makes the rule stricter); a medium-risk one goes through with a warning
  in the audit log
- Reports (`AuditReport`): suspicious operations (medium and high, riskiest first), a client's
  risk profile (operations by level, blocked, factors, overall level) and error statistics
  (attempts, errors by type, failed, blocked and retried, error rate); `render()` prints all three

### Demo simulation (Day 6)

`demo_simulation()` in `main.py` runs one bank through a whole day:

- **Setup**: 7 clients and 13 accounts of every type and in all five currencies; one account
  frozen, part of an investment account invested
- **Simulation**: 39 transactions through the queue, plus 3 rejected before it (negative amount,
  unsupported currency, the same sender and receiver). Among them:
  - failures: not enough money, a frozen or unknown account, below a savings minimum, invested
    money, an external transfer inside the bank, a regular account going into the minus
  - suspicious ones: large amounts and new receivers (allowed with a warning), a very large
    transfer, a night transfer to a new account and a burst of operations (blocked)
  - an urgent transaction, a deferred one retried after the night, and a cancelled one
- **Logging**: the queue writes `transaction_queued` and `transaction_cancelled` to the audit log
  (`TransactionQueue(audit_log=...)`); the demo prints the count of every event and the full
  trail of a completed, a failed, a blocked and a retried transaction
- **Client view**: John Smith's accounts, transaction history and suspicious operations
- **Reports** (`TransactionReport`): top-3 clients, transaction statistics (by status and type,
  success rate, volume, fees) and the total balance per currency and in USD.
  The report converts currencies with `ExchangeRates`; `Bank` itself still never mixes them

### Reports and charts (Day 7)

`ReportBuilder(bank, transactions, rates, audit_log=..., risk_analyzer=...)` in `report_builder.py`:

- **Report types**, each a `Report` with key figures (`summary`), a table (`rows`) and a text
  version (`text`); money is converted to `rates.base`:

  | Method | Summary | Rows (CSV) |
  |---|---|---|
  | `client_report(client_id)` | accounts and their value, total, transactions by status, risk profile | the client's transactions |
  | `bank_report(top=3)` | clients, accounts, total balance, balance by currency, top clients, transaction statistics | every client with their total, richest first |
  | `risk_report()` | thresholds, operations by risk level, factors, suspicious operations, client profiles, error statistics | every risk assessment, riskiest first |

  The risk report needs the audit log and the risk analyzer; the other two work without them
- **Export**: `export_to_json()` (Decimal as exact strings, dates in ISO format, enums as their
  values), `export_to_csv()` (UTF-8 with BOM so spreadsheet programs open it) and `export_to_text()`
- **Charts** (`charts()` returns matplotlib figures, `save_charts(folder, client_id=None)` saves them
  as PNG):
  - pie: money by currency, transactions by status, transactions by risk level, a client's accounts
  - bar: richest clients, transactions by type, risk factors, failed attempts by error
  - balance movement: the cash balance of the bank or of one client after each completed
    transaction, as a step line. The opening balance is worked out backwards from today's
    balances; invested money is not cash and is left out
  - `pie_chart()`, `bar_chart()` and `balance_chart()` also work on their own with any
    `{label: value}` data. Past 8 categories the rest is folded into "Other"; risk levels and
    statuses use fixed status colours (green, yellow, red, gray)
- The demo saves the reports of the Day 6 simulation and all ten charts to `output/`

## Project structure

```
├── README.md
├── requirements.txt
├── src/
│   ├── main.py        # demo
│   ├── models.py      # AccountStatus, Currency, Owner, AbstractAccount, BankAccount
│   ├── accounts.py    # SavingsAccount, PremiumAccount, InvestmentAccount, AssetType
│   ├── clients.py     # Client, ClientStatus
│   ├── bank.py        # Bank, SuspiciousActivity, ClientRank
│   ├── transactions.py  # Transaction, TransactionQueue, TransactionProcessor, ExchangeRates
│   ├── audit.py       # AuditLog, AuditEntry, AuditLevel
│   ├── risk.py        # RiskAnalyzer, RiskAssessment, RiskLevel, RiskFactor, ClientRiskProfile
│   ├── reports.py     # AuditReport, ErrorStatistics, TransactionReport, TransactionStatistics
│   ├── report_builder.py  # ReportBuilder, Report, BalancePoint, pie/bar/balance charts
│   ├── utils.py       # money and rate validation, ID generation, formatting
│   └── exceptions.py  # custom exceptions
├── tests/
│   ├── test_main.py       # BankAccount, utils
│   ├── test_accounts.py   # savings, premium and investment accounts
│   ├── test_bank.py       # clients, bank operations, logins, security rules, reports
│   ├── test_transactions.py  # transactions, queue, processor rules, ten transactions end to end
│   ├── test_audit.py      # audit log: levels, file, filters, bank integration
│   ├── test_risk.py       # risk factors, blocking, reports, ordinary and suspicious transactions
│   ├── test_reports.py    # queue logging, transaction report, the Day 6 simulation
│   └── test_report_builder.py  # report types, balance history, export, charts, the Day 7 demo
└── docs/
    └── algorithm.md   # algorithms and design
```

## Getting started

Requires Python 3.9+.

```bash
pip install -r requirements.txt
python src/main.py         # run the demo; reports and charts go to output/
python -m pytest           # run the tests
```

## Usage

```python
from models import AccountStatus, BankAccount, Owner

account = BankAccount(Owner("John Smith", "john@example.com"), balance=1000, currency="usd")
account.deposit("500.25")    # Decimal('1500.25')
account.withdraw(300)        # Decimal('1200.25')
print(account)
# Bank account | Client: John Smith | No.: ****3F9A | Status: active | Balance: 1 200.25 USD

account.freeze()
account.deposit(100)         # raises AccountFrozenError
```

### Savings, premium and investment accounts

```python
from accounts import InvestmentAccount, PremiumAccount, SavingsAccount

savings = SavingsAccount(Owner("Mary Williams"), balance=50_000, min_balance=10_000, monthly_rate="0.012")
savings.apply_monthly_interest()   # Decimal('600.00')
savings.withdraw(45_000)           # raises InsufficientFundsError: only 40 600 is above the minimum

premium = PremiumAccount(Owner("Jennifer Davis"), balance=1_000, overdraft_limit=20_000, fee=50)
premium.withdraw(15_000)           # Decimal('-14050.00'): overdraft used, fee added

investment = InvestmentAccount(Owner("Michael Wilson"), balance=200_000)
investment.invest("stocks", 100_000)
investment.invest("bonds", 30_000)
investment.invest("etf", 50_000)
projection = investment.project_yearly_growth({"stocks": 0.10, "bonds": 0.04, "etf": 0.07})
projection.total_growth            # Decimal('14700.00')
projection.projected_value         # Decimal('194700.00')
```

### Bank

```python
from datetime import date
from bank import Bank
from clients import Client
from accounts import SavingsAccount

bank = Bank("Training Bank")
john = bank.add_client(Client("John Smith", date(1985, 3, 12), "1234", phone="+1 555 010 2030"))
account = bank.open_account(john.client_id, SavingsAccount, balance=20_000, min_balance=5_000)
bank.authenticate_client(john.client_id, "1234")   # returns the client
bank.authenticate_client(john.client_id, "0000")   # AuthenticationError, attempts left: 2
bank.freeze_account(account.account_id)
bank.search_accounts(client_id=john.client_id, status="frozen")   # [account]
bank.get_clients_ranking("USD")    # [ClientRank(position=1, client=..., total=Decimal('20000.00'))]
```

`Bank` takes an optional `clock` (a function returning `datetime`), so the night rule
can be tested at any real time.

### Transactions

```python
from transactions import ExchangeRates, Transaction, TransactionProcessor, TransactionQueue

usd = bank.open_account(john.client_id, account_id="JOHN-1001", balance=5_000)
eur = bank.open_account(john.client_id, account_id="JOHN-1002", currency="EUR")

queue = TransactionQueue()
processor = TransactionProcessor(bank, rates=ExchangeRates({"EUR": "1.08"}))   # 1 EUR = 1.08 USD
queue.add(Transaction("transfer", 1_080, sender_id="JOHN-1001", receiver_id="JOHN-1002"))
queue.add(Transaction("external_transfer", 1_000, sender_id="JOHN-1001",
                      receiver_id="DE89370400440532", priority=5))
processor.run(queue)     # the external transfer goes first because of its priority
usd.balance              # Decimal('2910.00'): 5 000 − (1 000 + 1% fee) − 1 080
eur.balance              # Decimal('1000.00')
```

### Audit and risk

```python
from audit import AuditLog
from reports import AuditReport
from risk import RiskAnalyzer

audit_log = AuditLog("audit.jsonl")              # memory + file
bank = Bank("Training Bank", audit_log=audit_log)
# ... clients and accounts ...
analyzer = RiskAnalyzer(bank, rates=rates)
processor = TransactionProcessor(bank, rates=rates, audit_log=audit_log, risk_analyzer=analyzer)

tx = processor.process(Transaction("transfer", 120_000, sender_id="JOHN-1001", receiver_id="MARY-2001"))
tx.failure_reason      # 'SuspiciousOperationError: Transaction ... blocked, high risk: very large amount'
audit_log.filter(min_level="error", client_id=john.client_id)
print(AuditReport(audit_log, analyzer).render())
```

### Bank-wide reports

```python
from reports import TransactionReport

report = TransactionReport(bank, transactions, rates)   # currencies converted to rates.base
report.client_history(john.client_id)   # transactions touching John's accounts, oldest first
report.statistics()                     # TransactionStatistics(total=..., by_status=..., ...)
report.top_clients(3)                   # [ClientRank(position=1, client=..., total=...), ...]
report.total_balance()                  # all open accounts, in USD
print(report.render())
```

### Report builder

```python
from report_builder import ReportBuilder

builder = ReportBuilder(bank, transactions, rates, audit_log=audit_log, risk_analyzer=analyzer)
report = builder.client_report(john.client_id)     # also bank_report(), risk_report()
print(report.text)
builder.export_to_json(report, "output/john.json")
builder.export_to_csv(report, "output/john.csv")  # one row per transaction
builder.balance_history(john.client_id)           # [BalancePoint(position=0, ..., balance=...), ...]
builder.save_charts("output/charts", client_id=john.client_id)   # list of PNG paths
```

The demo (`python src/main.py`) creates two accounts of each type, runs valid and invalid
operations on them, and then calls `withdraw()`, `__str__()` and `get_account_info()`
on all of them through one loop. The growth rates in the demo (stocks 10%, bonds 4%,
etf 7%) are made-up values for the exercise. The bank part of the demo adds clients,
opens accounts, shows successful and failed logins with blocking, freezing, a night-time
attempt on a settable clock, search, reports and the suspicious activity log.
The transactions part queues ten transactions (a deposit, transfers with and without
conversion, an external transfer with a fee, a premium account going into the minus,
rejected transfers from a regular account and to a frozen one, an urgent withdrawal,
a deferred deposit and a cancelled one), then runs the queue in the afternoon, at 04:30
(the deferred deposit is rejected for the night and retried) and at 05:00, and prints
the balances and the error log. Its exchange rates are made-up values too.
The audit part runs twelve ordinary and suspicious transactions through the risk checks
(a large withdrawal, a very large transfer, a night transfer to a new account, a burst of
operations followed by a large external transfer), prints the warnings and blocked operations
from the audit log and the three reports, and reads the log file back from a temporary folder.
Then comes the Day 6 simulation described above, and the Day 7 reports built from its results:
the three reports are printed and saved to `output/` with all the charts.

See [docs/algorithm.md](docs/algorithm.md) for how validation, statuses and the class design work.
