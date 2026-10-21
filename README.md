# Bank Account Model

A training model of bank accounts for practising OOP.
`AbstractAccount` holds the shared state and rules; `BankAccount` is a concrete
single-currency account with input validation, status checks and auto-generated IDs.
`SavingsAccount`, `PremiumAccount` and `InvestmentAccount` extend `BankAccount`
with their own withdrawal rules and extra features.

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

## Project structure

```
├── README.md
├── requirements.txt
├── src/
│   ├── main.py        # demo
│   ├── models.py      # AccountStatus, Currency, Owner, AbstractAccount, BankAccount
│   ├── accounts.py    # SavingsAccount, PremiumAccount, InvestmentAccount, AssetType
│   ├── utils.py       # money and rate validation, ID generation, formatting
│   └── exceptions.py  # custom exceptions
├── tests/
│   ├── test_main.py       # BankAccount, utils
│   └── test_accounts.py   # savings, premium and investment accounts
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

The demo (`python src/main.py`) creates two accounts of each type, runs valid and invalid
operations on them, and then calls `withdraw()`, `__str__()` and `get_account_info()`
on all of them through one loop. The growth rates in the demo (stocks 10%, bonds 4%,
etf 7%) are made-up values for the exercise.

See [docs/algorithm.md](docs/algorithm.md) for how validation, statuses and the class design work.
