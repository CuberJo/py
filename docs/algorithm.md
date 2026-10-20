# Algorithms and Design

## Class overview

```
AccountError (Exception)
├── AccountFrozenError
├── AccountClosedError
├── InvalidOperationError
└── InsufficientFundsError

AbstractAccount (ABC)                 Owner (frozen dataclass)
│  _account_id, _owner,               AccountStatus (Enum): ACTIVE, FROZEN, CLOSED
│  _balance, _status                  Currency (Enum): RUB, USD, EUR, KZT, CNY
│  _ensure_operable()
│  _change_status()
│  deposit() / withdraw() / get_account_info()   ← abstract
└── BankAccount
       _currency, freeze(), unfreeze(), close(), __str__()
```

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
    if amount > balance: raise InsufficientFundsError
    balance -= amount
    return balance
```

All checks run before the balance is touched, so a failed operation leaves the account unchanged.

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
- **Open/closed.** New account types subclass `AbstractAccount` and reuse the status guard.
  Status rules are data (`_ALLOWED_TRANSITIONS`, `_BLOCKING_STATUSES`), so a subclass can
  extend them without editing existing methods. `account_type` is a class attribute
  that subclasses override for `__str__`.
- **Liskov substitution.** `BankAccount` keeps the `AbstractAccount` contract:
  same method signatures, both operations return the new balance, and errors come from
  the shared `AccountError` hierarchy.
- **Interface segregation.** `AbstractAccount` requires only the three operations every
  account needs; lifecycle methods (`freeze`, `unfreeze`, `close`) and `currency` are added
  by the concrete class.
- **Dependency inversion.** `BankAccount` gets its ID generator as a parameter
  (`id_generator: Callable[[], str]`) instead of calling `uuid` directly, so tests and
  other callers can provide their own.
