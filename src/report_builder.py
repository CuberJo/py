"""Report builder: client, bank and risk reports as text, JSON and CSV, plus charts."""

import csv
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

from audit import AuditLog
from bank import Bank
from exceptions import InvalidOperationError
from models import Currency
from reports import AuditReport, TransactionReport
from risk import RiskAnalyzer, RiskLevel
from transactions import ExchangeRates, Transaction, TransactionStatus
from utils import format_money

if TYPE_CHECKING:
    from matplotlib.figure import Figure

# Fixed categorical order: each series keeps its colour whatever else is on the chart
SERIES_COLORS = (
    "#2a78d6",  # blue
    "#eb6834",  # orange
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#e87ba4",  # magenta
    "#008300",  # green
    "#6250d6",  # violet
    "#e34948",  # red
)
# Risk levels are states, so they get the status colours, never series ones
RISK_COLORS = {RiskLevel.LOW: "#0ca30c", RiskLevel.MEDIUM: "#fab219", RiskLevel.HIGH: "#d03b3b"}
STATUS_COLORS = {
    TransactionStatus.COMPLETED: "#0ca30c",
    TransactionStatus.FAILED: "#d03b3b",
    TransactionStatus.CANCELLED: "#898781",
    TransactionStatus.PENDING: "#fab219",
}
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
MUTED_INK = "#52514e"
GRID = "#e1e0d9"
# Pie and bar charts fold everything past this many categories into "Other"
MAX_CATEGORIES = len(SERIES_COLORS)

Number = Union[int, float, Decimal]


class ReportKind(Enum):
    CLIENT = "client"
    BANK = "bank"
    RISK = "risk"


@dataclass(frozen=True)
class Report:
    """One generated report: key figures, a table of rows and the text version."""

    kind: ReportKind
    title: str
    generated_at: datetime
    summary: dict[str, Any]
    rows: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        """The report as plain JSON types: Decimal and datetime become strings."""
        return _plain(
            {
                "kind": self.kind,
                "title": self.title,
                "generated_at": self.generated_at,
                "summary": self.summary,
                "rows": self.rows,
            }
        )


@dataclass(frozen=True)
class BalancePoint:
    """The balance right after one completed transaction, in the base currency."""

    position: int  # 0 is the opening balance, then one per transaction in processing order
    transaction_id: Optional[str]
    timestamp: Optional[datetime]
    balance: Decimal


class ReportBuilder:
    """Builds client, bank and risk reports, exports them and draws charts.

    Money is converted to ``rates.base``. The risk report needs both the audit
    log and the risk analyzer; the other reports work without them.
    """

    def __init__(
        self,
        bank: Bank,
        transactions: Iterable[Transaction],
        rates: ExchangeRates,
        *,
        audit_log: Optional[AuditLog] = None,
        risk_analyzer: Optional[RiskAnalyzer] = None,
    ):
        if audit_log is not None and not isinstance(audit_log, AuditLog):
            raise InvalidOperationError("audit_log must be an AuditLog instance")
        if risk_analyzer is not None and not isinstance(risk_analyzer, RiskAnalyzer):
            raise InvalidOperationError("risk_analyzer must be a RiskAnalyzer instance")
        # TransactionReport checks the bank, the transactions and the rates
        self._transaction_report = TransactionReport(bank, transactions, rates)
        self._bank = bank
        self._transactions = list(transactions)
        self._rates = rates
        self._audit_log = audit_log
        self._risk_analyzer = risk_analyzer

    @property
    def base_currency(self) -> Currency:
        return self._rates.base

    # --- Reports ---

    def client_report(self, client_id: str) -> Report:
        client = self._bank.get_client(client_id)
        base = self.base_currency.value
        accounts = []
        for account in self._bank.search_accounts(client_id=client_id):
            value = getattr(account, "total_value", account.balance)  # invested money counts
            accounts.append(
                {
                    "account_id": account.account_id,
                    "type": account.account_type,
                    "currency": account.currency.value,
                    "status": account.status.value,
                    "balance": account.balance,
                    "value": value,
                    f"value_{base.lower()}": self._transaction_report.to_base(
                        value, account.currency
                    ),
                }
            )
        history = self._transaction_report.client_history(client_id)
        statuses = {status: 0 for status in TransactionStatus}
        for transaction in history:
            statuses[transaction.status] += 1
        summary: dict[str, Any] = {
            "client_id": client_id,
            "full_name": client.full_name,
            "status": client.status.value,
            "base_currency": base,
            "total": sum((a[f"value_{base.lower()}"] for a in accounts), Decimal("0.00")),
            "accounts": accounts,
            "transactions": len(history),
            "by_status": statuses,
        }
        if self._risk_analyzer is not None:
            profile = self._risk_analyzer.get_client_profile(client_id)
            summary["risk"] = {
                "level": profile.level,
                "max_score": profile.max_score,
                "operations": profile.operations,
                "blocked": profile.blocked,
                "factors": profile.factors,
            }
        rows = [_transaction_row(t) for t in history]

        lines = [f"Client: {client.full_name} ({client_id}), {client.status.value}"]
        lines.append("Accounts:")
        lines += [
            f"  {a['account_id']} | {a['type']} | {a['status']} | "
            f"{format_money(a['value'])} {a['currency']}"
            for a in accounts
        ] or ["  none"]
        lines.append(f"  total in {base}: {format_money(summary['total'])}")
        lines.append(
            f"Transactions: {len(history)} ("
            + ", ".join(f"{s.value} {n}" for s, n in statuses.items())
            + ")"
        )
        lines += [f"  {t.created_at:%Y-%m-%d %H:%M} | {t}" for t in history]
        if "risk" in summary:
            risk = summary["risk"]
            factors = ", ".join(f"{f.value} x{n}" for f, n in risk["factors"].items()) or "none"
            lines.append(
                f"Risk: {risk['level'].value}, max score {risk['max_score']}, "
                f"blocked {risk['blocked']}, factors: {factors}"
            )
        return self._report(
            ReportKind.CLIENT, f"Client report: {client.full_name}", summary, rows, lines
        )

    def bank_report(self, top: int = 3) -> Report:
        report = self._transaction_report
        stats = report.statistics()
        base = self.base_currency.value
        ranking = report.top_clients(len(self._bank.clients)) if self._bank.clients else []
        summary = {
            "bank": self._bank.name,
            "base_currency": base,
            "clients": len(self._bank.clients),
            "accounts": len(self._bank.search_accounts()),
            "total_balance": report.total_balance(),
            "balance_by_currency": self.balance_by_currency(),
            "top_clients": [
                {"position": r.position, "client_id": r.client.client_id, "total": r.total}
                for r in ranking[:top]
            ],
            "transactions": {
                "total": stats.total,
                "by_status": stats.by_status,
                "by_type": stats.by_type,
                "success_rate": stats.success_rate,
                "volume": stats.volume,
                "fees": stats.fees,
            },
        }
        rows = [
            {
                "position": r.position,
                "client_id": r.client.client_id,
                "full_name": r.client.full_name,
                "accounts": len(r.client.account_ids),
                f"total_{base.lower()}": r.total,
            }
            for r in ranking
        ]
        lines = [
            f"Bank: {self._bank.name}, {summary['clients']} clients, "
            f"{summary['accounts']} accounts",
            *report.render(top).splitlines(),
        ]
        return self._report(
            ReportKind.BANK, f"Bank report: {self._bank.name}", summary, rows, lines
        )

    def risk_report(self) -> Report:
        if self._audit_log is None or self._risk_analyzer is None:
            raise InvalidOperationError("The risk report needs an audit log and a risk analyzer")
        audit = AuditReport(self._audit_log, self._risk_analyzer)
        assessments = self._risk_analyzer.assessments()
        stats = audit.error_statistics()
        summary = {
            "base_currency": self._risk_analyzer.base_currency.value,
            "thresholds": self._risk_analyzer.describe_thresholds(),
            "assessed": len(assessments),
            "by_level": self.risk_levels(),
            "blocked": sum(1 for a in assessments if a.blocked),
            "factors": self.risk_factors(),
            "suspicious": [a.transaction_id for a in audit.suspicious_operations()],
            "clients": [
                {
                    "client_id": p.client_id,
                    "level": p.level,
                    "operations": p.operations,
                    "blocked": p.blocked,
                    "max_score": p.max_score,
                }
                for p in audit.client_risk_profiles()
            ],
            "errors": {
                "attempts": stats.attempts,
                "errors": stats.errors,
                "error_rate": stats.error_rate,
                "failed": stats.failed,
                "blocked": stats.blocked,
                "retried": stats.retried,
                "by_error": stats.by_error,
            },
        }
        rows = [
            {
                "transaction_id": a.transaction_id,
                "client_id": a.client_id,
                "timestamp": a.timestamp,
                "amount": a.amount,
                "score": a.score,
                "level": a.level,
                "factors": "; ".join(f.value for f in a.factors),
                "blocked": a.blocked,
            }
            for a in sorted(assessments, key=lambda a: -a.score)
        ]
        lines = [
            f"Thresholds: {summary['thresholds']}",
            f"Assessed {len(assessments)}: "
            + ", ".join(f"{lvl.value} {n}" for lvl, n in summary["by_level"].items())
            + f", blocked {summary['blocked']}",
            *audit.render().splitlines(),
        ]
        return self._report(ReportKind.RISK, "Risk report", summary, rows, lines)

    # --- Chart data ---

    def balance_by_currency(self) -> dict[Currency, Decimal]:
        """Open accounts of each currency that has any, converted to the base currency."""
        totals = {}
        for currency in Currency:
            amount = self._bank.get_total_balance(currency)
            if amount:
                totals[currency] = self._transaction_report.to_base(amount, currency)
        return totals

    def balance_history(self, client_id: Optional[str] = None) -> list[BalancePoint]:
        """Cash balance of a client's accounts (or the whole bank) after each completed transaction.

        The opening balance is worked out backwards from today's balances, so it
        also covers accounts opened with money. Invested money is not cash and
        is left out. Transactions processed at the same moment keep the order
        they were given in.
        """
        if client_id is not None:
            account_ids = set(self._bank.get_client(client_id).account_ids)
        else:
            account_ids = {a.account_id for a in self._bank.search_accounts()}
        completed = sorted(
            (t for t in self._transactions if t.status is TransactionStatus.COMPLETED),
            key=lambda t: t.processed_at,
        )
        changes = []
        for transaction in completed:
            if not {transaction.sender_id, transaction.receiver_id} & account_ids:
                continue
            change = Decimal("0.00")
            if transaction.sender_id in account_ids:
                sender = self._bank.get_account(transaction.sender_id)
                change -= self._transaction_report.to_base(
                    transaction.debited_amount, sender.currency
                )
            if transaction.receiver_id in account_ids:
                receiver = self._bank.get_account(transaction.receiver_id)
                change += self._transaction_report.to_base(
                    transaction.credited_amount, receiver.currency
                )
            changes.append((transaction, change))
        accounts = [self._bank.get_account(account_id) for account_id in account_ids]
        closing = sum(
            (self._transaction_report.to_base(a.balance, a.currency) for a in accounts),
            Decimal("0.00"),
        )
        balance = closing - sum((change for _, change in changes), Decimal("0.00"))
        points = [BalancePoint(0, None, None, balance)]
        for position, (transaction, change) in enumerate(changes, start=1):
            balance += change
            points.append(
                BalancePoint(
                    position, transaction.transaction_id, transaction.processed_at, balance
                )
            )
        return points

    def risk_levels(self) -> dict[RiskLevel, int]:
        counts = {level: 0 for level in RiskLevel}
        if self._risk_analyzer is not None:
            for assessment in self._risk_analyzer.assessments():
                counts[assessment.level] += 1
        return counts

    def risk_factors(self) -> dict[str, int]:
        """How many assessed transactions have each factor, most frequent first."""
        counts: dict[str, int] = {}
        if self._risk_analyzer is not None:
            for assessment in self._risk_analyzer.assessments():
                for factor in assessment.factors:
                    counts[factor.value] = counts.get(factor.value, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: -item[1]))

    # --- Charts ---

    def charts(self, client_id: Optional[str] = None) -> dict[str, "Figure"]:
        """Every chart for the bank, the risks if analysed, and the client if given; by file name."""
        base = self.base_currency.value
        stats = self._transaction_report.statistics()
        ranking = (
            self._transaction_report.top_clients(min(len(self._bank.clients), MAX_CATEGORIES))
            if self._bank.clients
            else []
        )
        charts = {
            "bank_balance_by_currency": pie_chart(
                {c.value: v for c, v in self.balance_by_currency().items()},
                f"Money by currency, in {base}",
            ),
            "bank_transactions_by_status": pie_chart(
                {s.value: n for s, n in stats.by_status.items()},
                "Transactions by status",
                colors=[STATUS_COLORS[s] for s in stats.by_status],
            ),
            "bank_top_clients": bar_chart(
                {r.client.full_name: r.total for r in ranking},
                f"Richest clients, {base}",
                horizontal=True,
            ),
            "bank_transactions_by_type": bar_chart(
                {t.value: n for t, n in stats.by_type.items()}, "Transactions by type"
            ),
            "bank_balance_history": balance_chart(
                self.balance_history(), f"Bank cash balance, {base}"
            ),
        }
        if self._risk_analyzer is not None:
            levels = self.risk_levels()
            charts["risk_levels"] = pie_chart(
                {lvl.value: n for lvl, n in levels.items()},
                "Transactions by risk level",
                colors=[RISK_COLORS[lvl] for lvl in levels],
            )
            charts["risk_factors"] = bar_chart(
                self.risk_factors(), "Risk factors found", horizontal=True
            )
        if self._audit_log is not None and self._risk_analyzer is not None:
            by_error = AuditReport(self._audit_log, self._risk_analyzer).error_statistics().by_error
            charts["risk_errors"] = bar_chart(by_error, "Failed attempts by error", horizontal=True)
        if client_id is not None:
            client = self._bank.get_client(client_id)
            summary = self.client_report(client_id).summary
            charts["client_accounts"] = pie_chart(
                {a["account_id"]: a[f"value_{base.lower()}"] for a in summary["accounts"]},
                f"{client.full_name}: accounts, in {base}",
            )
            charts["client_balance_history"] = balance_chart(
                self.balance_history(client_id), f"{client.full_name}: cash balance, {base}"
            )
        return charts

    # --- Export ---

    def export_to_json(self, report: Report, path: Union[str, Path]) -> Path:
        path = _prepare(path)
        with path.open("w", encoding="utf-8") as file:
            json.dump(report.to_dict(), file, ensure_ascii=False, indent=2)
        return path

    def export_to_csv(self, report: Report, path: Union[str, Path]) -> Path:
        """The report's rows as a table; the columns are every key, in the order first seen."""
        path = _prepare(path)
        rows = _plain(report.rows)
        columns = list(dict.fromkeys(key for row in rows for key in row))
        # utf-8-sig so spreadsheet programs detect the encoding
        with path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def export_to_text(self, report: Report, path: Union[str, Path]) -> Path:
        path = _prepare(path)
        path.write_text(report.text + "\n", encoding="utf-8")
        return path

    def save_charts(
        self, folder: Union[str, Path], client_id: Optional[str] = None, dpi: int = 100
    ) -> list[Path]:
        """Save every chart from ``charts()`` as a PNG file in ``folder``."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        saved = []
        for name, figure in self.charts(client_id).items():
            path = folder / f"{name}.png"
            figure.savefig(path, dpi=dpi, facecolor=SURFACE)
            saved.append(path)
        return saved

    def _report(
        self,
        kind: ReportKind,
        title: str,
        summary: dict[str, Any],
        rows: list[dict[str, Any]],
        lines: list[str],
    ) -> Report:
        generated_at = self._bank.now()
        header = [title, f"Generated: {generated_at:%Y-%m-%d %H:%M}", "=" * len(title)]
        return Report(kind, title, generated_at, summary, rows, "\n".join(header + lines))


# --- Charts: plain functions over {label: value}, so they work for any data ---


def pie_chart(
    data: dict[str, Number], title: str, *, colors: Optional[list[str]] = None
) -> "Figure":
    """A pie of the positive values; zero and negative ones (overdraft) are left out."""
    figure, ax = _new_figure(title)
    if colors is None:
        colors = list(SERIES_COLORS)
    labels, values, slice_colors = [], [], []
    for (label, value), color in zip(_fold(data), colors):
        if value > 0:
            labels.append(label)
            values.append(float(value))
            slice_colors.append(color)
    if not values:
        _no_data(ax)
        return figure
    wedges, _, autotexts = ax.pie(
        values,
        colors=slice_colors,
        autopct=lambda pct: f"{pct:.0f}%" if pct >= 4 else "",
        pctdistance=0.78,
        startangle=90,
        counterclock=False,
        wedgeprops={"width": 0.45, "edgecolor": SURFACE, "linewidth": 2},
        textprops={"color": INK, "fontsize": 9},
    )
    for text in autotexts:
        text.set_color("white")
    ax.legend(
        wedges,
        [f"{label} ({_short(value)})" for label, value in zip(labels, values)],
        loc="center left",
        bbox_to_anchor=(1, 0.5),
        frameon=False,
        labelcolor=MUTED_INK,
    )
    ax.set_aspect("equal")
    figure.tight_layout()
    return figure


def bar_chart(data: dict[str, Number], title: str, *, horizontal: bool = False) -> "Figure":
    """One bar per label, all in one colour since the bars are one series; values on the bars."""
    figure, ax = _new_figure(title)
    items = _fold(data)
    if not items:
        _no_data(ax)
        return figure
    labels = [label for label, _ in items]
    values = [float(value) for _, value in items]
    if horizontal:
        # The first item on top, as in a ranking
        bars = ax.barh(labels[::-1], values[::-1], color=SERIES_COLORS[0], height=0.6)
        ax.xaxis.grid(True, color=GRID)
        _thousands(ax.xaxis)
    else:
        bars = ax.bar(labels, values, color=SERIES_COLORS[0], width=0.6)
        ax.yaxis.grid(True, color=GRID)
        _thousands(ax.yaxis)
    ax.bar_label(bars, labels=[_short(v) for v in (values[::-1] if horizontal else values)],
                 padding=3, color=MUTED_INK, fontsize=9)  # fmt: skip
    baseline = ax.axvline if horizontal else ax.axhline
    baseline(0, color=MUTED_INK, linewidth=0.8)
    _style_axes(ax)
    figure.tight_layout()
    return figure


def balance_chart(points: list[BalancePoint], title: str) -> "Figure":
    """The balance as a step line: it changes only when a transaction completes."""
    figure, ax = _new_figure(title)
    if not points:
        _no_data(ax)
        return figure
    positions = [p.position for p in points]
    balances = [float(p.balance) for p in points]
    ax.plot(positions, balances, drawstyle="steps-post", color=SERIES_COLORS[0], linewidth=2)
    ax.plot(positions, balances, "o", color=SERIES_COLORS[0], markersize=4,
            markeredgecolor=SURFACE)  # fmt: skip
    # Label only the opening and the closing balance
    for point in (points[0], points[-1]):
        ax.annotate(
            _short(point.balance),
            (point.position, float(point.balance)),
            textcoords="offset points",
            xytext=(0, 8),
            ha="center",
            color=INK,
            fontsize=9,
        )
    ax.set_xlabel("Completed transactions, in processing order (0 = opening balance)",
                  color=MUTED_INK)  # fmt: skip
    ax.yaxis.grid(True, color=GRID)
    _thousands(ax.yaxis)
    ax.margins(y=0.15)
    _style_axes(ax)
    figure.tight_layout()
    return figure


def _new_figure(title: str) -> tuple["Figure", Any]:
    # Figure without pyplot: no GUI backend and no global state, so it works in tests and servers
    from matplotlib.figure import Figure

    figure = Figure(figsize=(8, 4.5), facecolor=SURFACE)
    ax = figure.add_subplot()
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", color=INK, fontsize=12, fontweight="bold")
    return figure, ax


def _style_axes(ax: Any) -> None:
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED_INK, labelsize=9)


def _thousands(axis: Any) -> None:
    """Tick labels with space-separated thousands, like ``format_money``: '62 500'."""
    from matplotlib.ticker import FuncFormatter

    def label(value: float, _: Any) -> str:
        # Count axes can get half steps (0.5, 1.5), so keep one decimal where there is one
        return f"{value:,.{0 if value == int(value) else 1}f}".replace(",", " ")

    axis.set_major_formatter(FuncFormatter(label))


def _no_data(ax: Any) -> None:
    ax.text(0.5, 0.5, "No data", ha="center", va="center", color=MUTED_INK, transform=ax.transAxes)
    ax.set_axis_off()


def _fold(data: dict[str, Number]) -> list[tuple[str, Number]]:
    """Keep the first ``MAX_CATEGORIES - 1`` items and add the rest up as "Other"."""
    items = list(data.items())
    if len(items) <= MAX_CATEGORIES:
        return items
    kept = items[: MAX_CATEGORIES - 1]
    return kept + [("Other", sum(value for _, value in items[MAX_CATEGORIES - 1 :]))]


def _short(value: Number) -> str:
    """Compact number for labels: 1 234 567 -> '1.2M', 12 500 -> '12.5K'."""
    number = float(value)
    for limit, suffix in ((1e6, "M"), (1e3, "K")):
        if abs(number) >= limit:
            return f"{number / limit:.1f}{suffix}"
    return f"{number:g}"


# --- Export helpers ---


def _transaction_row(transaction: Transaction) -> dict[str, Any]:
    info = transaction.get_transaction_info()
    return {key: info[key] for key in (
        "transaction_id", "created_at", "processed_at", "type", "amount", "currency", "fee",
        "sender_id", "receiver_id", "status", "failure_reason",
    )}  # fmt: skip


def _plain(value: Any) -> Any:
    """Turn Decimal, datetime and Enum values (also as dict keys) into JSON-friendly ones."""
    if isinstance(value, dict):
        return {_plain(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _prepare(path: Union[str, Path]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
