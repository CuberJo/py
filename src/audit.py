"""Audit log: events with a severity level, kept in memory and optionally in a file."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, Union

from exceptions import InvalidOperationError


class AuditLevel(Enum):
    INFO = "info"  # normal operations
    WARNING = "warning"  # allowed, but worth a look
    ERROR = "error"  # an operation failed
    CRITICAL = "critical"  # a dangerous operation was blocked

    @property
    def severity(self) -> int:
        """Position in the order INFO < WARNING < ERROR < CRITICAL."""
        return list(AuditLevel).index(self)

    @classmethod
    def parse(cls, value: Union["AuditLevel", str]) -> "AuditLevel":
        """Accept an AuditLevel member or a case-insensitive name such as 'Warning'."""
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).lower())
        except ValueError:
            allowed = ", ".join(level.value for level in cls)
            raise InvalidOperationError(
                f"Unknown audit level: {value!r}. Allowed: {allowed}"
            ) from None


@dataclass(frozen=True)
class AuditEntry:
    """One audit event: when, how serious, what happened and who was involved."""

    timestamp: datetime
    level: AuditLevel
    event: str  # short machine-readable name, such as 'transaction_blocked'
    message: str
    client_id: Optional[str] = None
    account_id: Optional[str] = None
    transaction_id: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly form: the time as ISO text, Decimal values as strings."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "level": self.level.value,
            "event": self.event,
            "message": self.message,
            "client_id": self.client_id,
            "account_id": self.account_id,
            "transaction_id": self.transaction_id,
            "details": {
                key: str(value) if isinstance(value, Decimal) else value
                for key, value in self.details.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditEntry":
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            level=AuditLevel.parse(data["level"]),
            event=data["event"],
            message=data["message"],
            client_id=data.get("client_id"),
            account_id=data.get("account_id"),
            transaction_id=data.get("transaction_id"),
            details=dict(data.get("details") or {}),
        )

    def __str__(self) -> str:
        text = f"{self.timestamp:%Y-%m-%d %H:%M} | {self.level.value.upper():8} | {self.event}"
        for label, value in (
            ("client", self.client_id),
            ("account", self.account_id),
            ("tx", self.transaction_id),
        ):
            if value is not None:
                text += f" | {label} {value}"
        return f"{text} | {self.message}"


class AuditLog:
    """Keeps every audit entry in memory and, if ``file_path`` is given, appends it to a file.

    The file has one JSON object per line (JSON Lines), so it can be appended to
    without rewriting it and read back with ``AuditLog.load()``.
    """

    def __init__(
        self,
        file_path: Optional[Union[str, Path]] = None,
        *,
        clock: Callable[[], datetime] = datetime.now,
    ):
        # The clock is injected so tests and the demo can control the time
        self._file_path = Path(file_path) if file_path is not None else None
        self._clock = clock
        self._entries: list[AuditEntry] = []

    @property
    def file_path(self) -> Optional[Path]:
        return self._file_path

    @property
    def entries(self) -> list[AuditEntry]:
        return list(self._entries)

    def log(
        self,
        level: Union[AuditLevel, str],
        event: str,
        message: str,
        *,
        timestamp: Optional[datetime] = None,
        client_id: Optional[str] = None,
        account_id: Optional[str] = None,
        transaction_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> AuditEntry:
        """Record an event; ``timestamp`` defaults to the log's clock."""
        if not isinstance(event, str) or not event.strip():
            raise InvalidOperationError("Audit event name cannot be empty")
        entry = AuditEntry(
            timestamp=timestamp or self._clock(),
            level=AuditLevel.parse(level),
            event=event.strip(),
            message=message,
            client_id=client_id,
            account_id=account_id,
            transaction_id=transaction_id,
            details=dict(details or {}),
        )
        self._entries.append(entry)
        if self._file_path is not None:
            with self._file_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        return entry

    def filter(
        self,
        *,
        level: Optional[Union[AuditLevel, str]] = None,
        min_level: Optional[Union[AuditLevel, str]] = None,
        event: Optional[str] = None,
        client_id: Optional[str] = None,
        account_id: Optional[str] = None,
        transaction_id: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> list[AuditEntry]:
        """Return the entries matching every given filter, oldest first.

        ``level`` matches one level exactly, ``min_level`` that level and more
        serious ones; ``since`` and ``until`` are inclusive.
        """
        exact = AuditLevel.parse(level) if level is not None else None
        minimum = AuditLevel.parse(min_level).severity if min_level is not None else None
        result = []
        for entry in self._entries:
            if exact is not None and entry.level is not exact:
                continue
            if minimum is not None and entry.level.severity < minimum:
                continue
            if event is not None and entry.event != event:
                continue
            if client_id is not None and entry.client_id != client_id:
                continue
            if account_id is not None and entry.account_id != account_id:
                continue
            if transaction_id is not None and entry.transaction_id != transaction_id:
                continue
            if since is not None and entry.timestamp < since:
                continue
            if until is not None and entry.timestamp > until:
                continue
            result.append(entry)
        return result

    @classmethod
    def load(
        cls, file_path: Union[str, Path], *, clock: Callable[[], datetime] = datetime.now
    ) -> "AuditLog":
        """Read a log file back; new entries are appended to the same file."""
        log = cls(file_path, clock=clock)
        with log._file_path.open(encoding="utf-8") as file:
            for number, line in enumerate(file, start=1):
                if not line.strip():
                    continue
                try:
                    log._entries.append(AuditEntry.from_dict(json.loads(line)))
                except (ValueError, KeyError, TypeError) as error:
                    raise InvalidOperationError(
                        f"Bad audit entry on line {number} of {file_path}: {error}"
                    ) from None
        return log

    def __len__(self) -> int:
        return len(self._entries)
