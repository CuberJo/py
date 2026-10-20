# Bank Account Model

An abstract bank account model that serves as a base for more complex account types.
`AbstractAccount` holds the shared state and rules; `BankAccount` is a concrete
single-currency account with input validation, status checks and auto-generated IDs.

## Features

- `AbstractAccount` with an account ID, owner, protected (read-only) balance and status
  (`active`, `frozen`, `closed`), plus abstract `deposit`, `withdraw`, `get_account_info`
- `BankAccount` with a currency (`RUB`, `USD`, `EUR`, `KZT`, `CNY`) and a short UUID
  generated when no account number is given
- Custom exceptions: `AccountFrozenError`, `AccountClosedError`,
  `InvalidOperationError`, `InsufficientFundsError`
- Amount checks: numbers only, positive, at most 2 decimal places, no NaN/infinity
- Money stored as `Decimal`, so there are no float rounding errors
- `__str__` shows account type, client, last 4 characters of the number, status, balance and currency

## Project structure

```
├── README.md
├── requirements.txt
├── src/
│   ├── main.py        # demo
│   ├── models.py      # AccountStatus, Currency, Owner, AbstractAccount, BankAccount
│   ├── utils.py       # money validation, ID generation, formatting
│   └── exceptions.py  # custom exceptions
├── tests/
│   └── test_main.py
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

account = BankAccount(Owner("Ivan Petrov", "ivan@example.com"), balance=1000, currency="usd")
account.deposit("500.25")    # Decimal('1500.25')
account.withdraw(300)        # Decimal('1200.25')
print(account)
# Bank account | Client: Ivan Petrov | No.: ****3F9A | Status: active | Balance: 1 200.25 USD

account.freeze()
account.deposit(100)         # raises AccountFrozenError
```

See [docs/algorithm.md](docs/algorithm.md) for how validation, statuses and the class design work.
