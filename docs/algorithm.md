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
├── OperationTimeError
├── TransactionNotFoundError
└── SuspiciousOperationError

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

```
Transaction (transactions.py)             TransactionQueue
   _transaction_id, _type, _amount,          _entries: transaction_id → (sequence, Transaction)
   _currency, _fee, _sender_id,              add(), cancel(), pending(), ready(), pop_next()
   _receiver_id, _status, _failure_reason,
   _priority, _attempts,                  TransactionProcessor
   _debited_amount, _credited_amount,        _bank, _rates: ExchangeRates
   _created_at, _scheduled_at,               _external_fee_rate, _max_attempts, _retry_delay
   _processed_at                             _errors: [ProcessingError]
   is_due(), cancel()                        calculate_fee(), process(), run(queue)

TransactionType (Enum): DEPOSIT, WITHDRAWAL, TRANSFER, EXTERNAL_TRANSFER
TransactionStatus (Enum): PENDING, COMPLETED, FAILED, CANCELLED
ExchangeRates: currency → value of one unit in the base currency
```

The processor moves money only through the bank (`deposit`, `withdraw`, `transfer`), so
the night rule, blocked clients, the large-operation flag and every account type's own
rules still apply. The only Day 4 changes to existing classes are `Bank.transfer()` and
`Bank.now()`.

```
AuditLog (audit.py)                       RiskAnalyzer (risk.py)
   _file_path, _clock                        _bank, _rates, _large_amount, _very_large_amount
   _entries: [AuditEntry]                    _frequency_limit, _frequency_window, _block_level
   log(), filter(), load()                   _history: [(Transaction, RiskAssessment)]
                                             assess(), check(), assessments(), get_client_profile()
AuditEntry (frozen dataclass)
   timestamp, level, event, message,      AuditReport (reports.py)
   client_id, account_id, transaction_id,    _audit_log, _risk_analyzer
   details; to_dict(), from_dict()           suspicious_operations(), client_risk_profile(),
                                             client_risk_profiles(), error_statistics(), render()
AuditLevel (Enum): INFO, WARNING, ERROR, CRITICAL
RiskLevel (Enum): LOW, MEDIUM, HIGH
RiskFactor (Enum): LARGE_AMOUNT, VERY_LARGE_AMOUNT, FREQUENT_OPERATIONS, NEW_RECEIVER, NIGHT_TIME
RiskAssessment, ClientRiskProfile, ErrorStatistics (frozen dataclasses)
```

Day 5 changes to existing classes: `Bank` takes an optional `audit_log` and gets
`get_account_owner_id()`; `TransactionProcessor` takes optional `audit_log` and `risk_analyzer`.
Without them both work exactly as before.

```
TransactionReport (reports.py)
   _bank, _transactions: [Transaction], _rates: ExchangeRates
   client_history(), statistics(), total_balance(), top_clients(), render()
TransactionStatistics (frozen dataclass): total, by_status, by_type, volume, fees, success_rate
```

Day 6 adds `TransactionReport` and gives `TransactionQueue` an optional `audit_log`;
nothing else changes.

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
| `transfer(sender, receiver, debit, credit)` | yes | both owners | flag if debit ≥ 1 000 000, withdraw from the sender, deposit to the receiver |

Account rules (frozen, closed, limits, minimum balance) are still checked by the account itself.

`transfer` is atomic: it remembers the sender's balance, calls `sender.withdraw(debit)` and
then `receiver.deposit(credit)`. If the deposit fails, it deposits `before − after` back to the
sender, which also returns a fee the sender's account added (premium), and re-raises the error.

## Transactions

### Transaction

A transaction is created as `PENDING` and is checked at once: the amount goes through
`to_money`, the currency through `Currency.parse`, and the accounts must match the type:

| Type | Sender | Receiver | Bank call |
|---|---|---|---|
| `deposit` | — | account of the bank | `deposit` |
| `withdrawal` | account of the bank | — | `withdraw` |
| `transfer` | account of the bank | another account of the bank | `transfer` |
| `external_transfer` | account of the bank | account in another bank (any string) | `withdraw` |

```
PENDING ──► COMPLETED   (processor: fee, debited and credited amounts, processed_at)
   │  ├───► FAILED      (processor: failure_reason = "ErrorName: message")
   │  └───► CANCELLED   (cancel(): reason)
   └── retry: stays PENDING, scheduled_at moves forward, attempts += 1
```

Final statuses cannot change: a second `cancel()` or processing a finished transaction raises
`InvalidOperationError`. `fee` is zero until the transaction completes, since a failed one
charges nothing.

### Queue

The queue keeps `transaction_id → (sequence, transaction)`; the sequence is a counter that
grows with every `add`. Processing order is the sort key `(−priority, sequence)`: higher
priority first, equal priority in the order added. A transaction is due when
`scheduled_at` is empty or not later than now, so a deferred one stays in the queue until
then. `pop_next(now)` removes and returns the first due transaction; `cancel(id)` removes
it and marks it `CANCELLED`. The queue sorts on every call, which is enough for a training
model; a heap would be the choice for many transactions.

With an `audit_log`, `add()` writes `transaction_queued` and `cancel()` writes
`transaction_cancelled`. A transaction the processor puts back for a retry is logged as
queued again, with its attempt number and the new time. The queue does not know the bank,
so these entries name the account but not the client.

### Processor

```
process(transaction):                    # one attempt
    attempts += 1; now = bank.now()
    fee = amount × external_fee_rate if external_transfer else 0     # rounded to cents
    try:
        look up the sender and receiver   # external receiver must not be in this bank
        both not FROZEN / CLOSED          # else AccountFrozenError / AccountClosedError
        debit  = convert(amount + fee, tx currency → sender currency)
        credit = convert(amount,       tx currency → receiver currency)
        sender not premium and debit > balance → InsufficientFundsError
        bank.deposit / withdraw / transfer
    except BankError as error:
        log ProcessingError(now, id, attempt, error, will_retry)
        will_retry = OperationTimeError and attempts < max_attempts
        will_retry → scheduled_at = now + retry_delay       # stays PENDING
        otherwise  → FAILED with the reason
    else: COMPLETED

run(queue):
    now = bank.now()
    while (tx := queue.pop_next(now)):
        process(tx); if still PENDING: queue.add(tx)        # back with the new time
```

Notes:

- All rules are checked before the bank call, so a rejected transaction moves no money.
- The negative-balance rule repeats what most account types already refuse, so it holds for
  any account type that might be added later. A premium account may go below zero within
  its overdraft, and may keep sending money while it is already in the minus.
- An external transfer pays `amount + fee`; the other bank gets `amount`. A premium sender
  also pays its own per-withdrawal `fee`, since the account's `withdraw` rules apply.
- Conversion: `ExchangeRates` stores what one unit of each currency is worth in the base
  currency (`USD` = 1). `convert(a, X, Y) = round_money(a × rate[X] / rate[Y])`. Same
  currency needs no rates; a missing rate fails the transaction with `InvalidOperationError`.
- Only the night rule is retried, since waiting fixes it; other errors (frozen account,
  no money, unknown account) will not change by themselves. A retry gets a new sequence
  number, so it goes after transactions of the same priority that were already queued.
- Time comes from `bank.now()`, so the processor follows the same clock as the night rule.

## Audit log

```
log(level, event, message, timestamp?, client_id?, account_id?, transaction_id?, details?):
    entry = AuditEntry(timestamp or clock(), AuditLevel.parse(level), ...)
    entries.append(entry)
    if file_path: append json.dumps(entry.to_dict()) + "\n" to the file
```

- Levels are ordered by `severity` (INFO 0 … CRITICAL 3), so `filter(min_level="error")`
  returns ERROR and CRITICAL. All filters are combined with AND; `since` and `until` are inclusive.
- The file is JSON Lines: one object per line, so a new entry is appended without rewriting
  the file. Times are ISO strings and `Decimal` details are strings, so nothing is lost to
  float rounding. `load()` reads it back, reports the line number of a broken entry, and keeps
  appending to the same file.

What goes to the log:

| Source | Event | Level |
|---|---|---|
| `Bank._flag` (logins, blocking, night attempts, large operations) | `suspicious_activity` | warning |
| queue: added (or put back for a retry) | `transaction_queued` | info |
| queue: cancelled | `transaction_cancelled` | info |
| processor: completed | `transaction_completed` | info |
| processor: risk factors found, allowed | `risk_flagged` | info if low, warning if medium |
| processor: night rejection that will be retried | `transaction_retry` | warning |
| processor: failed for good | `transaction_failed` | error |
| processor: blocked as dangerous | `transaction_blocked` | critical |

## Risk analysis

```
assess(transaction, now):
    client = owner of the sender (or of the receiver for a deposit)
    amount = convert(transaction amount → base currency)   # no rate: compare as is
    amount ≥ very_large (100 000) → VERY_LARGE_AMOUNT   (4)
    amount ≥ large      (10 000)  → LARGE_AMOUNT        (2)
    other operations of the client created in [created_at − 10 min, created_at]
        ≥ frequency_limit (3)     → FREQUENT_OPERATIONS (2)
    transfer to another client's account or external transfer,
        receiver not among the client's completed transfers → NEW_RECEIVER (1)
    created_at in 23:00–06:00     → NIGHT_TIME          (1)
    score = sum of points; level = LOW (< 2), MEDIUM (< 4), HIGH
    blocked = level ≥ block_level (HIGH)

check(transaction, now):   assess + remember (transaction, assessment) in the history
```

Notes:

- Frequency and night use `created_at`, the moment the client made the operation. The queue
  may process many transactions at once, and a deferred deposit may run at night; neither
  says anything about the client's behaviour.
- The analyzer's night (23:00–06:00) is wider than the bank's closed hours (00:00–05:00):
  the bank refuses to work at night, the analyzer marks the hours around it as risky too.
- A blocked attempt still counts towards frequency, so a client hammering the bank with
  rejected operations is flagged. A receiver becomes known only after a completed transfer,
  so a failed or blocked one does not make it trusted.
- A night-time retry is assessed again on every attempt; `assessments()` returns only the latest
  assessment of each transaction, so reports count every transaction once.

## Blocking dangerous operations

```
process(transaction):
    attempts += 1; now = bank.now()
    try:
        if risk_analyzer:
            assessment = risk_analyzer.check(transaction, now)
            assessment.blocked → SuspiciousOperationError
            factors found      → audit risk_flagged
        execute as before
    except BankError: ... as before; audit retry / failed / blocked
    else: audit completed
```

`SuspiciousOperationError` is not in `RETRYABLE_ERRORS`, so a blocked transaction fails at
once, no money moves, and it appears in the processor's `error_log` like any other failure.

## Reports

- **Suspicious operations**: latest assessments at `min_level` (medium) or above, sorted by
  score, highest first; equal scores keep the order they were checked in.
- **Client risk profile**: the client's transactions counted by level and by factor, the number
  blocked, the highest score, and the overall level, which is the highest level the client
  reached. `client_risk_profiles()` lists every client with transactions, riskiest first.
- **Error statistics**: counted from the audit log's processor events. Attempts are all
  completed, retry, failed and blocked events; errors are all but completed, grouped by
  exception name; the error rate is errors / attempts.

`TransactionReport` works over a list of transactions and the bank. `Bank` never mixes
currencies, so this report converts them to `rates.base` (USD):

- **Client history**: the transactions whose sender or receiver is one of the client's
  accounts, sorted by `created_at`.
- **Statistics**: counts by status and by type; volume and fees add up only completed
  transactions; the success rate is completed / (completed + failed), so cancelled ones
  do not count against it.
- **Total balance**: `Bank.get_total_balance()` of each currency, converted and added.
- **Top clients**: `Bank.get_clients_ranking()` of each currency, converted and added per
  client, then sorted the same way (total descending, ties by name). A premium account in
  overdraft makes a total negative; `ExchangeRates.convert()` accepts only positive amounts,
  so the report converts the absolute value and puts the sign back.

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
  In `transactions.py`, `Transaction` holds data and its status, `TransactionQueue` only orders
  and stores, and `TransactionProcessor` applies the transaction rules.
  `AuditLog` only stores and filters entries, `RiskAnalyzer` only scores, and `AuditReport`
  only summarises what the other two collected.
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
  depend on the real time. `TransactionProcessor` gets the bank and the `ExchangeRates`
  as parameters, and its fee rate, retry count and delay are settings. The audit log and the
  risk analyzer are passed in the same way, so the processor works with or without them, and
  the risk thresholds and weights are settings and class attributes, not hard-coded checks.
