"""Exported files are opened in spreadsheet software, so user text in them must
stay text. No database is used: the data layer is replaced with test rows."""
import csv
import io
from decimal import Decimal

import pytest

import api
import database

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

ROWS = [
    (1, "16.10.2026, 10:00:00", "Расход", Decimal("10.00"), '=HYPERLINK("http://evil.test","x")'),
    (2, "16.10.2026, 11:00:00", "Расход", Decimal("5.50"), "+SUM(1,2)"),
    (3, "16.10.2026, 12:00:00", "Расход", Decimal("1.00"), "-2+3"),
    (4, "16.10.2026, 13:00:00", "Расход", Decimal("1.00"), "@cmd"),
    (5, "16.10.2026, 14:00:00", "Доход", Decimal("1000.00"), "Зарплата"),
    (6, "16.10.2026, 15:00:00", "Доход", Decimal("7.00"), None),
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api.app.router, "on_startup", [])
    monkeypatch.setitem(api.app.dependency_overrides, api.get_current_user,
                        lambda: {"id": 1, "email": "user@example.test"})
    monkeypatch.setattr(database, "get_operations", lambda user_id: ROWS)
    return TestClient(api.app)


def test_csv_export_keeps_formula_like_text_as_text(client):
    response = client.get("/export/csv?period=all")
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0] == ["ID", "Дата", "Тип", "Сумма", "Категория"]
    assert [row[4] for row in rows[1:]] == [
        '\'=HYPERLINK("http://evil.test","x")', "'+SUM(1,2)", "'-2+3", "'@cmd", "Зарплата", "",
    ]
    # Amounts, dates and types are not touched.
    assert [row[3] for row in rows[1:]] == ["10.00", "5.50", "1.00", "1.00", "1000.00", "7.00"]
    assert rows[1][1] == "16.10.2026, 10:00:00" and rows[1][2] == "Расход"


@pytest.mark.parametrize("value,expected", [
    ("=1+1", "'=1+1"), ("\tx", "'\tx"), ("\rx", "'\rx"), ("Еда", "Еда"), ("", ""), (None, None), (Decimal("-1"), Decimal("-1")),
])
def test_spreadsheet_text(value, expected):
    assert api._spreadsheet_text(value) == expected


def test_export_needs_the_authorization_header(monkeypatch):
    monkeypatch.setattr(api.app.router, "on_startup", [])
    response = TestClient(api.app).get("/export/csv?period=all")
    assert response.status_code == 401


def test_export_period_is_validated_before_it_reaches_the_file_name(client):
    response = client.get("/export/csv?period=all%0d%0aX-Injected:%201")
    assert response.status_code == 422
    assert "x-injected" not in response.headers


class _FakeSheet:
    def __init__(self):
        self.rows = []
        self.title = ""

    def append(self, row):
        self.rows.append(row)


class _FakeWorkbook:
    created = []

    def __init__(self):
        self.active = _FakeSheet()
        _FakeWorkbook.created.append(self)

    def save(self, stream):
        stream.write(b"xlsx")


def test_excel_export_keeps_formula_like_text_as_text(client, monkeypatch):
    # openpyxl is not a dependency yet; a stand-in records what the export writes.
    import sys
    import types
    monkeypatch.setitem(sys.modules, "openpyxl", types.SimpleNamespace(Workbook=_FakeWorkbook))
    _FakeWorkbook.created.clear()
    response = client.get("/export/excel?period=all")
    assert response.status_code == 200
    rows = _FakeWorkbook.created[0].active.rows
    assert [row[4] for row in rows[1:]] == [
        '\'=HYPERLINK("http://evil.test","x")', "'+SUM(1,2)", "'-2+3", "'@cmd", "Зарплата", "",
    ]
    assert [row[3] for row in rows[1:]] == [10.0, 5.5, 1.0, 1.0, 1000.0, 7.0], "amounts stay numbers"
    assert all(isinstance(row[3], float) and isinstance(row[0], int) for row in rows[1:])


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_every_formula_prefix_is_neutralised_in_csv(client, monkeypatch, prefix):
    monkeypatch.setattr(database, "get_operations",
                        lambda user_id: [(1, "16.10.2026", "Расход", Decimal("3.00"), prefix + "1+1")])
    rows = list(csv.reader(io.StringIO(client.get("/export/csv").text)))
    assert rows[1][4] == "'" + prefix + "1+1"
    assert rows[1][3] == "3.00"
