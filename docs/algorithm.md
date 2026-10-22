# Algorithms and Design

## Class overview

```
BankError (Exception)
├── AccountError
│   ├── AccountFrozenError
│   ├── AccountClosedError
│   ├── InvalidOperationError
│   │   └── AgeRestrictionError
│   ├── InsufficientFundsError
│   └── LimitExceededError
├── ClientNotFoundError
├── AccountNotFoundError
├── AuthenticationError
├── ClientBlockedError
└── OperationTimeError

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

```
Client (clients.py)                       Bank (bank.py)
   _client_id, _full_name, _birth_date       _clients:        client_id → Client
   _phone, _email, _status                   _accounts:       account_id → BankAccount
   _pin_salt, _pin_hash                      _account_owners: account_id → client_id
   _account_ids                              _failed_logins:  client_id → count
   check_pin(), block(), unblock()           _suspicious:     [SuspiciousActivity]
   link_account(), as_owner()                _clock:          () → datetime
```

The bank holds clients and accounts; accounts do not know about the bank, so the
account classes did not change for Day 3.

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

## Client

- The age is counted in full years: `today.year − birth.year`, minus one if this year's
  birthday has not come yet. Under 18 → `AgeRestrictionError`; a future birth date is rejected.
  `today` can be passed to the constructor so the check is testable on fixed dates.
- Phone: spaces, dashes and brackets are removed, then it must be an optional `+` and
  10–15 digits. Email is checked by `Owner` (it must contain `@`).
- PIN: 4–6 digits. Only `sha256(salt + pin)` with a random 16-byte salt is kept;
  `check_pin` compares hashes with `hmac.compare_digest`.
- `account_ids` is returned as a tuple, so accounts are linked only by the bank.
  Closed accounts stay in the list as history.

## Bank operations

Every operation on an existing account goes through the same checks, in this order:

```
_get_operable_account(account_id, action):
    account = get_account(account_id)        # unknown → AccountNotFoundError
    _ensure_working_hours(action)            # 00:00 ≤ now < 05:00 → flag + OperationTimeError
    _get_active_client(owner)                # blocked → ClientBlockedError
    return account
```

| Method | Night check | Owner must be active | Then |
|---|---|---|---|
| `open_account(client_id, account_class, **kwargs)` | yes | yes | create with `client.as_owner()`, register, link to client |
| `close_account` | yes | yes | `account.close()` (zero balance required) |
| `freeze_account` | no | no | `account.freeze()` — protective, always allowed |
| `unfreeze_account` | yes | yes | `account.unfreeze()` |
| `deposit` / `withdraw` | yes | yes | flag if amount ≥ 1 000 000, then the account's own method |

Account rules (frozen, closed, limits, minimum balance) are still checked by the account itself.

## Authentication

```
authenticate_client(client_id, pin):
    unknown ID      → flag, AuthenticationError()      # same error as a wrong PIN
    client blocked  → flag, ClientBlockedError
    right PIN       → reset the counter, return client
    wrong PIN       → counter += 1
                      counter == 3 → block client, reset counter, flag, ClientBlockedError
                      otherwise    → AuthenticationError(attempts_left = 3 − counter)
```

The counter is per client and counts wrong PINs in a row. `unblock_client()` makes the
client active again and resets the counter.

## Suspicious activity

`SuspiciousActivity(timestamp, reason, client_id, account_id)` is appended to the log for:
blocking after wrong PINs, a login while blocked, a login with an unknown ID, a night attempt
(the operation is also rejected) and a deposit or withdrawal of 1 000 000 or more
(the operation still runs). `get_suspicious_activities(client_id=None)` returns the log,
optionally for one client. The time comes from the bank's `clock`.

## Search and reports

- `search_accounts(**filters)` goes through all accounts in opening order and keeps those
  that match every given filter: client, owner name (case-insensitive substring), status,
  currency, account type (subclasses match), balance range.
- `get_total_balance(currency)` sums open accounts of one currency. For an investment
  account it uses `total_value` (cash + invested), since invested money still belongs to the client.
- `get_clients_ranking(currency)` sums the same values per client and sorts by total,
  descending; equal totals are ordered by name. Only clients with open accounts in that
  currency are ranked.

## Account number

- If no number is given, `BankAccount` calls its `id_generator`, which by default is
  `utils.generate_short_id`: the first 12 hex characters of `uuid4()`, uppercase.
- A given number is stripped and must be at least 4 characters long, so
  `__str__` can always show the last 4 (`****4312`).

## SOLID

- **Single responsibility.** Validation, ID generation and formatting live in `utils.py`;
  exceptions in `exceptions.py`; the account classes only hold state and apply rules.
  Day 2 account types live in their own module, `accounts.py`; clients live in `clients.py`,
  and `bank.py` only coordinates clients and accounts and applies the security rules.
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
  source of market data. `Bank` gets its `clock` the same way, so the night rule does not
  depend on the real time.
