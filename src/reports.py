"""Audit reports: suspicious operations, client risk profiles and error statistics."""

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Union

from audit import AuditLog
from exceptions import InvalidOperationError
from risk import ClientRiskProfile, RiskAnalyzer, RiskAssessment, RiskLevel
from utils import format_percent

# Processor events that end an attempt; their count is the number of attempts
ATTEMPT_EVENTS = (
    "transaction_completed",
    "transaction_failed",
    "transaction_retry",
    "transaction_blocked",
)


@dataclass(frozen=True)
class ErrorStatistics:
    """Failed transaction attempts, counted from the audit log."""

    attempts: int
    errors: int
    retried: int  # rejected for now, will be tried again
    failed: int  # failed for good
    blocked: int  # blocked as dangerous
    by_error: dict[str, int]  # exception name -> count, most frequent first

    @property
    def error_rate(self) -> Decimal:
        """Share of attempts that ended in an error, as a fraction."""
        if not self.attempts:
            return Decimal(0)
        return Decimal(self.errors) / Decimal(self.attempts)


class AuditReport:
    """Builds reports from the audit log and the risk analyzer's assessments."""

    def __init__(self, audit_log: AuditLog, risk_analyzer: RiskAnalyzer):
        if not isinstance(audit_log, AuditLog):
            raise InvalidOperationError("audit_log must be an AuditLog instance")
        if not isinstance(risk_analyzer, RiskAnalyzer):
            raise InvalidOperationError("risk_analyzer must be a RiskAnalyzer instance")
        self._audit_log = audit_log
        self._risk_analyzer = risk_analyzer

    def suspicious_operations(
        self, min_level: Union[RiskLevel, str] = RiskLevel.MEDIUM
    ) -> list[RiskAssessment]:
        """Transactions assessed at ``min_level`` or above, highest score first."""
        found = self._risk_analyzer.assessments(min_level=min_level)
        # sorted() is stable, so equal scores keep the order they were checked in
        return sorted(found, key=lambda a: -a.score)

    def client_risk_profile(self, client_id: str) -> ClientRiskProfile:
        return self._risk_analyzer.get_client_profile(client_id)

    def client_risk_profiles(self) -> list[ClientRiskProfile]:
        """Profiles of every client with assessed transactions, riskiest first."""
        client_ids = dict.fromkeys(
            a.client_id for a in self._risk_analyzer.assessments() if a.client_id is not None
        )
        profiles = [self.client_risk_profile(client_id) for client_id in client_ids]
        return sorted(profiles, key=lambda p: (-p.level.severity, -p.max_score, p.client_id))

    def error_statistics(self) -> ErrorStatistics:
        attempts = errors = retried = failed = blocked = 0
        by_error: Counter[str] = Counter()
        for entry in self._audit_log.entries:
            if entry.event not in ATTEMPT_EVENTS:
                continue
            attempts += 1
            if entry.event == "transaction_completed":
                continue
            errors += 1
            by_error[entry.details.get("error", "unknown")] += 1
            if entry.event == "transaction_retry":
                retried += 1
            elif entry.event == "transaction_blocked":
                blocked += 1
            else:
                failed += 1
        return ErrorStatistics(
            attempts, errors, retried, failed, blocked, dict(by_error.most_common())
        )

    def render(self, client_id: Optional[str] = None) -> str:
        """The three reports as text; ``client_id`` limits the profiles to one client."""
        lines = ["Suspicious operations:"]
        suspicious = self.suspicious_operations()
        lines += [f"  {a}" for a in suspicious] or ["  none"]

        lines.append("Client risk profiles:")
        profiles = (
            [self.client_risk_profile(client_id)]
            if client_id is not None
            else self.client_risk_profiles()
        )
        for profile in profiles:
            levels = ", ".join(f"{lvl.value} {n}" for lvl, n in profile.by_level.items())
            factors = ", ".join(f"{f.value} x{n}" for f, n in profile.factors.items()) or "none"
            lines.append(
                f"  {profile.client_id}: {profile.level.value} risk | "
                f"operations {profile.operations} ({levels}) | blocked {profile.blocked} | "
                f"max score {profile.max_score} | factors: {factors}"
            )
        if not profiles:
            lines.append("  none")

        stats = self.error_statistics()
        lines.append("Error statistics:")
        lines.append(
            f"  attempts {stats.attempts}, errors {stats.errors} "
            f"({format_percent(stats.error_rate)}): failed {stats.failed}, "
            f"blocked {stats.blocked}, retried {stats.retried}"
        )
        lines += [f"  {name}: {count}" for name, count in stats.by_error.items()]
        return "\n".join(lines)
