"""Bank client: personal data, contacts, PIN and the numbers of their accounts."""

import hashlib
import hmac
import re
import secrets
from datetime import date
from enum import Enum
from typing import Callable, Optional

from exceptions import AgeRestrictionError, InvalidOperationError
from models import Owner
from utils import generate_short_id

MIN_CLIENT_AGE = 18
PIN_PATTERN = re.compile(r"\d{4,6}")
PHONE_PATTERN = re.compile(r"\+?\d{10,15}")
# Characters people type between phone digits; removed before validation
PHONE_SEPARATORS = re.compile(r"[\s\-()]")


class ClientStatus(Enum):
    ACTIVE = "active"
    BLOCKED = "blocked"


def age_on(birth_date: date, day: date) -> int:
    """Full years between birth_date and day."""
    had_birthday = (day.month, day.day) >= (birth_date.month, birth_date.day)
    return day.year - birth_date.year - (0 if had_birthday else 1)


def _hash_pin(pin: str, salt: bytes) -> bytes:
    return hashlib.sha256(salt + pin.encode()).digest()


class Client:
    """A bank client aged 18 or older.

    The PIN is stored only as a salted hash. Account numbers are linked
    by the bank when it opens an account; closed accounts stay in the list
    as history.
    """

    def __init__(
        self,
        full_name: str,
        birth_date: date,
        pin: str,
        *,
        phone: Optional[str] = None,
        email: Optional[str] = None,
        client_id: Optional[str] = None,
        status: ClientStatus = ClientStatus.ACTIVE,
        today: Optional[date] = None,
        id_generator: Callable[[], str] = generate_short_id,
    ):
        # ``today`` is injectable so the age check can be tested on fixed dates
        if not isinstance(full_name, str) or not full_name.strip():
            raise InvalidOperationError("Client name cannot be empty")
        if not isinstance(birth_date, date):
            raise InvalidOperationError(f"birth_date must be a date, got: {birth_date!r}")
        today = today or date.today()
        if birth_date > today:
            raise InvalidOperationError(f"Birth date is in the future: {birth_date}")
        age = age_on(birth_date, today)
        if age < MIN_CLIENT_AGE:
            raise AgeRestrictionError(age, MIN_CLIENT_AGE)
        if not isinstance(pin, str) or not PIN_PATTERN.fullmatch(pin):
            raise InvalidOperationError("PIN must be a string of 4 to 6 digits")
        if not isinstance(status, ClientStatus):
            raise InvalidOperationError(f"Unknown client status: {status!r}")
        if client_id is None:
            client_id = id_generator()
        if not isinstance(client_id, str) or not client_id.strip():
            raise InvalidOperationError("Client ID must be a non-empty string")

        self._client_id = client_id.strip()
        self._full_name = full_name.strip()
        self._birth_date = birth_date
        self._phone = self._normalize_phone(phone)
        # Owner validates the email the same way as for accounts
        self._email = Owner(self._full_name, email).email
        self._status = status
        self._pin_salt = secrets.token_bytes(16)
        self._pin_hash = _hash_pin(pin, self._pin_salt)
        self._account_ids: list[str] = []

    @staticmethod
    def _normalize_phone(phone: Optional[str]) -> Optional[str]:
        if phone is None:
            return None
        if not isinstance(phone, str):
            raise InvalidOperationError(f"Invalid phone: {phone!r}")
        normalized = PHONE_SEPARATORS.sub("", phone)
        if not PHONE_PATTERN.fullmatch(normalized):
            raise InvalidOperationError(f"Invalid phone: {phone!r}")
        return normalized

    @property
    def client_id(self) -> str:
        return self._client_id

    @property
    def full_name(self) -> str:
        return self._full_name

    @property
    def birth_date(self) -> date:
        return self._birth_date

    @property
    def phone(self) -> Optional[str]:
        return self._phone

    @property
    def email(self) -> Optional[str]:
        return self._email

    @property
    def contacts(self) -> dict[str, Optional[str]]:
        return {"phone": self._phone, "email": self._email}

    @property
    def status(self) -> ClientStatus:
        return self._status

    @property
    def is_blocked(self) -> bool:
        return self._status is ClientStatus.BLOCKED

    @property
    def account_ids(self) -> tuple[str, ...]:
        # A tuple, so accounts are linked only through the bank
        return tuple(self._account_ids)

    def age(self, today: Optional[date] = None) -> int:
        return age_on(self._birth_date, today or date.today())

    def check_pin(self, pin: str) -> bool:
        if not isinstance(pin, str):
            return False
        # Constant-time comparison does not leak how many bytes matched
        return hmac.compare_digest(_hash_pin(pin, self._pin_salt), self._pin_hash)

    def link_account(self, account_id: str) -> None:
        if account_id not in self._account_ids:
            self._account_ids.append(account_id)

    def block(self) -> None:
        self._status = ClientStatus.BLOCKED

    def unblock(self) -> None:
        self._status = ClientStatus.ACTIVE

    def as_owner(self) -> Owner:
        """Owner details for the accounts of this client."""
        return Owner(self._full_name, self._email)

    def get_client_info(self) -> dict[str, object]:
        return {
            "client_id": self._client_id,
            "full_name": self._full_name,
            "birth_date": self._birth_date.isoformat(),
            "status": self._status.value,
            "contacts": self.contacts,
            "account_ids": list(self._account_ids),
        }

    def __str__(self) -> str:
        return (
            f"Client {self._full_name} | ID: {self._client_id} | "
            f"Status: {self._status.value} | Accounts: {len(self._account_ids)}"
        )

    def __repr__(self) -> str:
        return (
            f"Client(full_name={self._full_name!r}, client_id={self._client_id!r}, "
            f"status={self._status})"
        )
