import csv
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

# Make modules from src/ importable when running pytest from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from audit import AuditLog  # noqa: E402
from bank import Bank  # noqa: E402
from clients import Client  # noqa: E402
from exceptions import ClientNotFoundError, InvalidOperationError  # noqa: E402
from report_builder import (  # noqa: E402
    BalancePoint,
    ReportBuilder,
    ReportKind,
    balance_chart,
    bar_chart,
    pie_chart,
)
from risk import RiskLevel  # noqa: E402
from transactions import (  # noqa: E402
    ExchangeRates,
    Transaction,
    TransactionProcessor,
    TransactionStatus,
)

DAYTIME = datetime(2026, 10, 22, 14, 0)
RATES = ExchangeRates({"EUR": "1.10", "RUB": "0.01"})


@pytest.fixture
def audit_log() -> AuditLog:
    return AuditLog(clock=lambda: DAYTIME)


@pytest.fixture
def bank(audit_log: AuditLog) -> Bank:
    bank = Bank("Test Bank", clock=lambda: DAYTIME, audit_log=audit_log)
    bank.add_client(Client("John Smith", date(1985, 3, 12), "1234", client_id="CL-001"))
    bank.add_client(Client("Mary Williams", date(1992, 7, 30), "5678", client_id="CL-002"))
    bank.open_account("CL-001", account_id="J-USD", balance=20_000)
    bank.open_account("CL-001", account_id="J-EUR", balance=1_000, currency="EUR")
    bank.open_account("CL-002", account_id="M-USD", balance=500)
    return bank


@pytest.fixture
def builder(bank: Bank, audit_log: AuditLog) -> ReportBuilder:
    from risk import RiskAnalyzer

    analyzer = RiskAnalyzer(bank, rates=RATES)
    processor = TransactionProcessor(bank, rates=RATES, audit_log=audit_log, risk_analyzer=analyzer)
    transactions = [
        Transaction("deposit", 100, receiver_id="J-USD", transaction_id="T-1", created_at=DAYTIME),
        Transaction(
            "transfer", 100, "EUR", sender_id="J-EUR", receiver_id="J-USD",
            transaction_id="T-2", created_at=DAYTIME,
        ),
        Transaction(
            "transfer", 12_000, sender_id="J-USD", receiver_id="M-USD",
            transaction_id="T-3", created_at=DAYTIME,
        ),  # large amount to a new receiver: allowed with a warning
        Transaction(
            "withdrawal", 20_000, sender_id="M-USD", transaction_id="T-4", created_at=DAYTIME
        ),  # not enough money
    ]  # fmt: skip
    for tx in transactions:
        processor.process(tx)
    return ReportBuilder(bank, transactions, RATES, audit_log=audit_log, risk_analyzer=analyzer)


# --- Reports ---


def test_client_report(builder):
    report = builder.client_report("CL-001")
    assert report.kind is ReportKind.CLIENT
    assert report.title == "Client report: John Smith"
    assert report.generated_at == DAYTIME
    summary = report.summary
    # 20 000 + 100 + 110 - 12 000 USD, 900 EUR * 1.10
    assert summary["total"] == Decimal("9200.00")
    assert [a["account_id"] for a in summary["accounts"]] == ["J-USD", "J-EUR"]
    assert summary["accounts"][1]["value_usd"] == Decimal("990.00")
    assert summary["by_status"][TransactionStatus.COMPLETED] == 3
    assert summary["risk"]["level"] is RiskLevel.MEDIUM
    assert [row["transaction_id"] for row in report.rows] == ["T-1", "T-2", "T-3"]
    assert "Client: John Smith (CL-001), active" in report.text
    assert "total in USD: 9 200.00" in report.text
    with pytest.raises(ClientNotFoundError):
        builder.client_report("CL-404")


def test_bank_report(builder):
    report = builder.bank_report(top=1)
    assert report.kind is ReportKind.BANK
    summary = report.summary
    assert (summary["clients"], summary["accounts"]) == (2, 3)
    assert summary["total_balance"] == Decimal("21700.00")  # 8 210 + 900 EUR + 12 500
    assert [c["client_id"] for c in summary["top_clients"]] == ["CL-002"]
    assert summary["transactions"]["total"] == 4
    # Every client is in the table, even past the top
    assert [row["client_id"] for row in report.rows] == ["CL-002", "CL-001"]
    assert "Bank: Test Bank, 2 clients, 3 accounts" in report.text
    assert "Top-1 clients" in report.text


def test_risk_report(builder):
    report = builder.risk_report()
    summary = report.summary
    assert summary["assessed"] == 4
    assert summary["by_level"] == {RiskLevel.LOW: 2, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 0}
    assert summary["suspicious"] == ["T-3", "T-4"]
    assert summary["errors"]["by_error"] == {"InsufficientFundsError": 1}
    assert report.rows[0]["transaction_id"] == "T-3"  # riskiest first
    assert report.rows[0]["factors"] == "large amount; transfer to a new receiver"
    assert "Assessed 4: low 2, medium 2, high 0, blocked 0" in report.text


def test_risk_report_needs_audit_log_and_analyzer(bank):
    with pytest.raises(InvalidOperationError):
        ReportBuilder(bank, [], RATES).risk_report()


@pytest.mark.parametrize(
    "kwargs",
    [{"bank": "bank"}, {"rates": {}}, {"audit_log": "audit.jsonl"}, {"risk_analyzer": object()}],
)
def test_builder_rejects_bad_arguments(bank, kwargs):
    arguments = {"bank": bank, "transactions": [], "rates": RATES, **kwargs}
    with pytest.raises(InvalidOperationError):
        ReportBuilder(**arguments)


# --- Balance history ---


def test_client_balance_history(builder):
    history = builder.balance_history("CL-001")
    # Opening: 20 000 USD + 1 000 EUR; T-2 moves money between John's own accounts
    assert [(p.position, p.transaction_id, p.balance) for p in history] == [
        (0, None, Decimal("21100.00")),
        (1, "T-1", Decimal("21200.00")),
        (2, "T-2", Decimal("21200.00")),
        (3, "T-3", Decimal("9200.00")),
    ]
    assert history[-1].timestamp == DAYTIME


def test_bank_balance_history(builder):
    history = builder.balance_history()
    # Only the deposit brings money in; transfers stay inside the bank
    assert history[0].balance == Decimal("21600.00")
    assert history[-1].balance == Decimal("21700.00")
    assert history[-1].balance == builder.bank_report().summary["total_balance"]


def test_balance_history_without_transactions(bank):
    assert ReportBuilder(bank, [], RATES).balance_history("CL-002") == [
        BalancePoint(0, None, None, Decimal("500.00"))
    ]


# --- Export ---


def test_export_to_json(builder, tmp_path):
    report = builder.client_report("CL-001")
    path = builder.export_to_json(report, tmp_path / "out" / "client.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["kind"] == "client"
    assert data["generated_at"] == "2026-10-22T14:00:00"
    assert data["summary"]["total"] == "9200.00"  # Decimal kept exact, as a string
    assert data["summary"]["by_status"]["completed"] == 3
    assert data["rows"][0]["type"] == "deposit"


def test_export_to_csv(builder, tmp_path):
    report = builder.risk_report()
    path = builder.export_to_csv(report, tmp_path / "risk.csv")
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 4
    assert list(rows[0]) == [
        "transaction_id", "client_id", "timestamp", "amount", "score", "level", "factors",
        "blocked",
    ]  # fmt: skip
    assert (rows[0]["transaction_id"], rows[0]["level"], rows[0]["amount"]) == (
        "T-3",
        "medium",
        "12000.00",
    )


def test_export_to_csv_without_rows(bank, tmp_path):
    report = ReportBuilder(bank, [], RATES).client_report("CL-002")
    path = ReportBuilder(bank, [], RATES).export_to_csv(report, tmp_path / "empty.csv")
    assert path.read_text(encoding="utf-8-sig").strip() == ""


def test_export_to_text(builder, tmp_path):
    report = builder.bank_report()
    path = builder.export_to_text(report, tmp_path / "bank.txt")
    assert path.read_text(encoding="utf-8").startswith("Bank report: Test Bank\nGenerated:")


# --- Charts ---


def test_save_charts(builder, tmp_path):
    saved = builder.save_charts(tmp_path / "charts", client_id="CL-001")
    assert [p.name for p in saved] == [
        "bank_balance_by_currency.png",
        "bank_transactions_by_status.png",
        "bank_top_clients.png",
        "bank_transactions_by_type.png",
        "bank_balance_history.png",
        "risk_levels.png",
        "risk_factors.png",
        "risk_errors.png",
        "client_accounts.png",
        "client_balance_history.png",
    ]
    for path in saved:
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_save_charts_without_risk_data(bank, tmp_path):
    saved = ReportBuilder(bank, [], RATES).save_charts(tmp_path)
    assert len(saved) == 5
    assert not any(p.name.startswith(("risk", "client")) for p in saved)


def test_pie_chart_leaves_out_non_positive_values():
    figure = pie_chart({"USD": Decimal("100"), "EUR": 0, "RUB": Decimal("-5")}, "Money")
    legend = [t.get_text() for t in figure.axes[0].get_legend().get_texts()]
    assert legend == ["USD (100)"]


def test_pie_chart_folds_extra_categories_into_other():
    figure = pie_chart({f"C{i}": 1 for i in range(10)}, "Many")
    legend = [t.get_text() for t in figure.axes[0].get_legend().get_texts()]
    assert legend[-1] == "Other (3)"
    assert len(legend) == 8


def test_bar_chart_labels_values():
    figure = bar_chart({"deposit": 12, "transfer": 15_000}, "Types")
    labels = [t.get_text() for t in figure.axes[0].texts]
    assert labels == ["12", "15.0K"]


def test_balance_chart_labels_opening_and_closing():
    points = [
        BalancePoint(0, None, None, Decimal("1000")),
        BalancePoint(1, "T-1", DAYTIME, Decimal("2500")),
    ]
    figure = balance_chart(points, "Balance")
    assert [t.get_text() for t in figure.axes[0].texts] == ["1.0K", "2.5K"]


@pytest.mark.parametrize(
    "figure",
    [pie_chart({}, "Empty"), bar_chart({}, "Empty"), balance_chart([], "Empty")],
)
def test_charts_without_data(figure):
    assert [t.get_text() for t in figure.axes[0].texts] == ["No data"]


# --- The Day 7 demo ---


def test_demo_reports(tmp_path, capsys):
    import main

    main.demo_reports(main.demo_simulation(), tmp_path)
    out = capsys.readouterr().out
    assert "=== Reports: Client report: John Smith ===" in out
    assert "=== Reports: Risk report ===" in out
    assert "bank.txt, bank.json, bank.csv (7 rows)" in out
    assert "John Smith's cash balance: 59 320.00 -> 49 220.00 USD" in out
    names = {p.name for p in tmp_path.iterdir()}
    assert {"client_CL-001.json", "bank.csv", "risk.txt", "charts"} <= names
    assert len(list((tmp_path / "charts").glob("*.png"))) == 10
