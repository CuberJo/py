# Algorithms and Design

## Class overview

```
AccountError (Exception)
├── AccountFrozenError
├── AccountClosedError
├── InvalidOperationError
├── InsufficientFundsError
└── LimitExceededError

AbstractAccount (ABC)                 Owner (frozen dataclass)
│  _account_id, _owner,               AccountStatus (Enum): ACTIVE, FROZEN, CLOSED
│  _balance, _status                  Currency (Enum): RUB, USD, EUR, KZT, CNY
│  _ensure_operable()                 AssetType (Enum): STOCKS, BONDS, ETF
│  _change_status()                   GrowthProjection (frozen dataclass)
│  deposit() / withdraw() / get_account_info()   ← abstract
└── BankAccount
    │  _currency, _debit(), freeze(), unfreeze(), close(), __str__()
    ├── SavingsAccount      _min_balance, _monthly_rate, apply_monthly_interest()
    ├── PremiumAccount      _withdrawal_limit, _overdraft_limit, _fee
    └── InvestmentAccount   _portfolio, invest(), divest(), project_yearly_growth()
```

`SavingsAccount`, `PremiumAccount` and `InvestmentAccount` live in `accounts.py` and
override `withdraw()`, `get_account_info()` and `__str__()`.

## Amount validation (`utils.to_money`)

Every amount — initial balance, deposit, withdrawal — goes through one function:

1. Reject `bool` and anything that is not `int`, `float`, `str` or `Decimal`.
   (`bool` is a subclass of `int` in Python, so `True` would otherwise count as 1.)
2. Convert to `Decimal` via `str(value)`, so `0.1` becomes exactly `0.1`
   instead of `0.1000000000000000055...`. Unparseable strings are rejected.
3. Reject `NaN` and infinity.
4. Reject negative amounts.
5. Reject zero, except for the initial balance (`allow_zero=True`).
6. Reject amounts more precise than one cent (`10.001`) instead of silently rounding.
7. Return the amount with exactly two decimal places.

On failure it raises `InvalidOperationError` and the balance is not changed.

## Deposit and withdrawal

```
deposit(amount):
    _ensure_operable()            # FROZEN → AccountFrozenError, CLOSED → AccountClosedError
    balance += to_money(amount)
    return balance

withdraw(amount):
    _ensure_operable()
    amount = to_money(amount)
    return _debit(amount, available=balance)

_debit(total, available):        # shared by every account type
    if total > available: raise InsufficientFundsError(available, total)
    balance -= total
    return balance
```

All checks run before the balance is touched, so a failed operation leaves the account unchanged.

Each account type overrides `withdraw()` and differs only in what it passes to `_debit`:

| Type | `total` | `available` | Extra check |
|---|---|---|---|
| `BankAccount` | amount | balance | — |
| `SavingsAccount` | amount | balance − min_balance (whole balance if the amount equals it) | — |
| `PremiumAccount` | amount + fee | balance + overdraft_limit | amount ≤ withdrawal_limit, else `LimitExceededError` |
| `InvestmentAccount` | amount | balance (free cash only) | — |

## Rates (`utils.to_rate`)

Rates are fractions (`0.05` = 5%). Like amounts, they are converted to `Decimal` via
`str()`, and `bool`, non-numbers, NaN and infinity are rejected. Ranges:

- savings monthly rate: from 0 to 1;
- yearly growth rate: from −1 (lose everything) with no upper bound.

Calculated money (interest, growth) is rounded to cents with banker's rounding
(`utils.round_money`).

## SavingsAccount

- At creation the balance must be at least `min_balance`.
- `apply_monthly_interest()`: checks the status, credits `round_money(balance × monthly_rate)`
  and returns the credited interest. Calling it every month compounds the interest.
- Withdrawing the whole balance at once is allowed, so the account can be emptied and closed.

## PremiumAccount

- Every withdrawal costs a fixed `fee`, charged together with the amount in one `_debit`.
- The balance may become negative down to `-overdraft_limit`; `close()` still requires
  a zero balance, so the debt must be repaid first.

## InvestmentAccount

The balance is free cash. The portfolio is a dict `AssetType → Decimal` that starts at zero
for every asset type.

```
invest(asset_type, amount):
    _ensure_operable()
    asset = AssetType.parse(asset_type)      # only stocks / bonds / etf
    amount = to_money(amount)                # positive, cents
    _debit(amount, available=balance)        # enough free cash
    portfolio[asset] += amount

divest(asset_type, amount):                  # the reverse: holding → free cash
    ...; if amount > portfolio[asset]: raise InsufficientFundsError

project_yearly_growth(growth_rates):
    parse every key to AssetType (unknown or duplicate keys → error)
    check every rate with to_rate(minimum=-1)
    for each asset with money in it:
        rate must be given, growth = round_money(holding × rate)
    return GrowthProjection(by_asset, total_growth, invested + total_growth)
```

The projection only reads the portfolio and does not change it. `close()` additionally
requires an empty portfolio.

## Status transitions

```
          freeze()
ACTIVE ─────────────► FROZEN
   ▲  ◄─────────────    │
   │      unfreeze()    │
   │ close()            │ close()
   ▼                    ▼
 CLOSED ◄───────────────┘   (terminal: no further changes)
```

- Allowed transitions are listed in `AbstractAccount._ALLOWED_TRANSITIONS`.
- Any change from `CLOSED` raises `AccountClosedError`.
- A transition not in the table (e.g. freezing a frozen account) raises `InvalidOperationError`.
- `close()` additionally requires a zero balance so money cannot get stuck on a closed account.

## Account number

- If no number is given, `BankAccount` calls its `id_generator`, which by default is
  `utils.generate_short_id`: the first 12 hex characters of `uuid4()`, uppercase.
- A given number is stripped and must be at least 4 characters long, so
  `__str__` can always show the last 4 (`****4312`).

## SOLID

- **Single responsibility.** Validation, ID generation and formatting live in `utils.py`;
  exceptions in `exceptions.py`; the account classes only hold state and apply rules.
  Day 2 account types live in their own module, `accounts.py`.
- **Open/closed.** The Day 2 account types were added by subclassing `BankAccount`;
  the only change to existing code was extracting `_debit` from `withdraw` so the
  subclasses can reuse it. Status rules are data (`_ALLOWED_TRANSITIONS`,
  `_BLOCKING_STATUSES`), so a subclass can extend them without editing existing methods.
  `account_type` is a class attribute that subclasses override for `__str__`.
- **Liskov substitution.** Every subclass keeps the `BankAccount` contract:
  same method signatures, `withdraw` returns the new balance, a rejected operation leaves
  the account unchanged, and errors come from the shared `AccountError` hierarchy.
  `get_account_info()` returns the base fields plus extra ones, never fewer, and
  the demo runs `withdraw`, `__str__` and `get_account_info` on a mixed list of accounts.
- **Interface segregation.** `AbstractAccount` requires only the three operations every
  account needs; lifecycle methods (`freeze`, `unfreeze`, `close`) and `currency` are added
  by the concrete class, and type-specific operations (`apply_monthly_interest`,
  `invest`, `project_yearly_growth`) exist only on the type that needs them.
- **Dependency inversion.** `BankAccount` gets its ID generator as a parameter
  (`id_generator: Callable[[], str]`) instead of calling `uuid` directly, so tests and
  other callers can provide their own. `project_yearly_growth` gets the growth rates
  as an argument instead of looking them up, so the account does not depend on any
  source of market data.
