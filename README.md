# Bank Account Model

A training model of bank accounts for practising OOP.
`AbstractAccount` holds the shared state and rules; `BankAccount` is a concrete
single-currency account with input validation, status checks and auto-generated IDs.
`SavingsAccount`, `PremiumAccount` and `InvestmentAccount` extend `BankAccount`
with their own withdrawal rules and extra features.
`Bank` manages `Client`s and their accounts, with PIN logins and security rules.

All data lives in memory: there is no database, no external API and no real market data.

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
  closed accounts do not, and currencies are never mixed because the model has no exchange rates
- Security:
  - 3 wrong PINs in a row block the client; a blocked client cannot log in or operate until
    `unblock_client()`; a successful login resets the counter
  - no account operations from 00:00 to 05:00 (`OperationTimeError`); freezing is the exception,
    since it protects the client, and logging in is allowed
  - suspicious activity log (`get_suspicious_activities()`): blocking after failed logins, logins
    while blocked or with an unknown ID, night attempts, deposits and withdrawals of 1 000 000 or more

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
│   ├── utils.py       # money and rate validation, ID generation, formatting
│   └── exceptions.py  # custom exceptions
├── tests/
│   ├── test_main.py       # BankAccount, utils
│   ├── test_accounts.py   # savings, premium and investment accounts
│   └── test_bank.py       # clients, bank operations, logins, security rules, reports
└── docs/
    └── algorithm.md   # algorithms and design
```

## Getting started

Requires Python 3.9+.

```bash
pip install -r requirements.txt
python src/main.py         # run the demo
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

The demo (`python src/main.py`) creates two accounts of each type, runs valid and invalid
operations on them, and then calls `withdraw()`, `__str__()` and `get_account_info()`
on all of them through one loop. The growth rates in the demo (stocks 10%, bonds 4%,
etf 7%) are made-up values for the exercise. The bank part of the demo adds clients,
opens accounts, shows successful and failed logins with blocking, freezing, a night-time
attempt on a settable clock, search, reports and the suspicious activity log.

See [docs/algorithm.md](docs/algorithm.md) for how validation, statuses and the class design work.
