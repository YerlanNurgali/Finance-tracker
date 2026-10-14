"""Bank CSV import: parser, database layer and HTTP flow.

All statement data below is synthetic. The database is an in-memory fake with
transaction semantics; nothing connects to a real server.
"""
import logging
from decimal import Decimal

import pytest

import bank_import
import database
from bank_import import ImportFileError, ImportSettings, parse_statement

SIGNED = dict(date_column=0, description_column=1, amount_column=2)


def csv_bytes(*lines, delimiter=";", bom=False):
    text = "\n".join(delimiter.join(cells) for cells in lines) + "\n"
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")


def parse(raw, currency="KZT", **settings):
    return parse_statement(raw, ImportSettings(**settings), currency)


def codes(result):
    return [(error["line"], error["code"]) for error in result.errors]


# --- Parser -------------------------------------------------------------------


def test_utf8_bom_and_semicolon_are_detected():
    raw = csv_bytes(("Дата", "Описание", "Сумма"), ("15.01.2026", "Кофе", "-1 200,50"), bom=True)
    result = parse(raw, **SIGNED)
    assert result.delimiter == ";"
    assert result.columns == ["Дата", "Описание", "Сумма"]  # BOM is not part of the header
    row, = result.rows
    assert (row.date, row.operation_type, row.amount, row.description) == (
        "15.01.2026, 00:00:00", "Расход", Decimal("1200.50"), "Кофе")


def test_comma_delimiter_with_quoted_fields():
    raw = b'Date,Description,Amount\n2026-01-15,"Shop, Inc.","-1,234.50"\n'
    result = parse(raw, date_format="YYYY-MM-DD", decimal_separator=".", **SIGNED)
    assert result.delimiter == ","
    assert result.rows[0].description == "Shop, Inc."
    assert result.rows[0].amount == Decimal("1234.50")


def test_explicit_delimiter_overrides_detection():
    raw = b"Date;Amount\n15.01.2026;100\n"
    result = parse(raw, delimiter=",", date_column=0, amount_column=0)
    assert result.columns == ["Date;Amount"]


@pytest.mark.parametrize("date_format,value,expected", [
    ("DD.MM.YYYY", "05.03.2026", "05.03.2026, 00:00:00"),
    ("DD.MM.YYYY", "05.03.2026 14:30", "05.03.2026, 14:30:00"),
    ("DD.MM.YYYY", "05.03.2026, 14:30:15", "05.03.2026, 14:30:15"),
    ("DD/MM/YYYY", "05/03/2026", "05.03.2026, 00:00:00"),
    ("MM/DD/YYYY", "03/05/2026", "05.03.2026, 00:00:00"),
    ("DD-MM-YYYY", "05-03-2026", "05.03.2026, 00:00:00"),
    ("YYYY-MM-DD", "2026-03-05", "05.03.2026, 00:00:00"),
    ("YYYY-MM-DD", "2026-03-05T09:15:00", "05.03.2026, 09:15:00"),
])
def test_date_formats(date_format, value, expected):
    raw = csv_bytes(("d", "a"), (value, "10"))
    result = parse(raw, date_format=date_format, date_column=0, amount_column=1)
    assert result.rows[0].date == expected


@pytest.mark.parametrize("value", ["31.02.2026", "2026-01-15", "15.01.1969", "завтра", "15.13.2026"])
def test_invalid_dates_are_reported(value):
    raw = csv_bytes(("d", "a"), (value, "10"))
    result = parse(raw, date_column=0, amount_column=1)
    assert result.rows == []
    assert result.errors == [{"line": 2, "code": "invalid_date", "column": 0}]


@pytest.mark.parametrize("value,separator,expected", [
    ("1 234,56", "auto", "1234.56"),
    ("1 234,56", "auto", "1234.56"),
    ("1.234.567,89", ",", "1234567.89"),
    ("1,234,567.89", ".", "1234567.89"),
    ("-1,234.56", "auto", "-1234.56"),
    ("(500)", "auto", "-500.00"),
    ("500-", "auto", "-500.00"),
    ("−42,10", "auto", "-42.10"),
    ("+100", "auto", "100.00"),
    ("₸ 1 000", "auto", "1000.00"),
    ("12.5 KZT", "auto", "12.50"),
    ("1,234", ",", "1.23"),
    ("1,234", ".", "1234.00"),
    ("0,005", ",", "0.01"),
])
def test_number_formats(value, separator, expected):
    assert bank_import.parse_amount(value, separator) == Decimal(expected)


@pytest.mark.parametrize("value,separator,code", [
    ("1,234", "auto", "ambiguous_number"),
    ("1.234", "auto", "ambiguous_number"),
    ("1,5", ".", "invalid_amount"),
    ("abc", "auto", "invalid_amount"),
    ("12,34,56", ",", "invalid_amount"),
    ("1 000 000 000 000", "auto", "amount_too_large"),
])
def test_invalid_numbers(value, separator, code):
    with pytest.raises(bank_import._RowError) as raised:
        bank_import.parse_amount(value, separator)
    assert raised.value.code == code


def test_signed_amounts_and_inverted_sign():
    raw = csv_bytes(("d", "t", "a"), ("15.01.2026", "Зарплата", "500 000"), ("16.01.2026", "Еда", "-3 500"))
    normal = parse(raw, **SIGNED)
    assert [(r.operation_type, r.amount) for r in normal.rows] == [
        ("Доход", Decimal("500000.00")), ("Расход", Decimal("3500.00"))]
    inverted = parse(raw, invert_sign=True, **SIGNED)
    assert [r.operation_type for r in inverted.rows] == ["Расход", "Доход"]


def test_type_column_mode():
    raw = csv_bytes(
        ("d", "a", "kind"),
        ("15.01.2026", "-100", "Списание"),
        ("16.01.2026", "200", " cr "),
        ("17.01.2026", "300", "Перевод"),
    )
    result = parse(raw, amount_mode="type_column", date_column=0, amount_column=1, type_column=2,
                   income_values="CR, Пополнение", expense_values="списание,DR")
    assert [(r.operation_type, r.amount) for r in result.rows] == [
        ("Расход", Decimal("100.00")), ("Доход", Decimal("200.00"))]
    assert result.errors == [{"line": 4, "code": "unknown_type", "column": 2}]


def test_debit_credit_mode():
    raw = csv_bytes(
        ("d", "in", "out"),
        ("15.01.2026", "1 000", ""),
        ("16.01.2026", "", "250,5"),
        ("17.01.2026", "10", "10"),
        ("18.01.2026", "", ""),
    )
    result = parse(raw, amount_mode="debit_credit", date_column=0, income_column=1, expense_column=2)
    assert [(r.operation_type, r.amount) for r in result.rows] == [
        ("Доход", Decimal("1000.00")), ("Расход", Decimal("250.50"))]
    assert codes(result) == [(4, "ambiguous_amount"), (5, "zero_amount")]


def test_currency_column():
    raw = csv_bytes(
        ("d", "a", "cur"),
        ("15.01.2026", "-100", "KZT"),
        ("16.01.2026", "-200", "₸"),
        ("17.01.2026", "-300", ""),
        ("18.01.2026", "-400", "USD"),
        ("19.01.2026", "-500", "XYZ"),
    )
    result = parse(raw, date_column=0, amount_column=1, currency_column=2)
    assert [r.amount for r in result.rows] == [Decimal("100.00"), Decimal("200.00"), Decimal("300.00")]
    assert codes(result) == [(5, "currency_mismatch"), (6, "currency_mismatch")]

    usd = parse(raw, currency="USD", date_column=0, amount_column=1, currency_column=2)
    assert [r.amount for r in usd.rows] == [Decimal("300.00"), Decimal("400.00")]


def test_empty_rows_are_skipped_not_errors():
    raw = b"d;a\n\n15.01.2026;10\n ; \n\n16.01.2026;-5\n"
    result = parse(raw, date_column=0, amount_column=1)
    assert result.total_rows == 2
    assert result.empty_rows == 3
    assert result.errors == []
    assert [r.line for r in result.rows] == [3, 6]


def test_short_row_reports_missing_value():
    raw = b"d;desc;a\n15.01.2026;only two\n"
    result = parse(raw, **SIGNED)
    assert result.errors == [{"line": 2, "code": "missing_value", "column": 2}]


def test_rows_without_header():
    raw = b"15.01.2026;-10\n16.01.2026;20\n"
    result = parse(raw, has_header=False, date_column=0, amount_column=1)
    assert result.columns == ["", ""]
    assert [r.line for r in result.rows] == [1, 2]


@pytest.mark.parametrize("raw,code", [
    ("Дата;Сумма\n15.01.2026;10\n".encode("cp1251"), "invalid_encoding"),
    (b"\xff\xfe\x00d\x00a", "invalid_encoding"),
    (b"d;a\n15.01.2026;1\x00\n", "invalid_content"),
    (b"", "empty_file"),
    (b"\xef\xbb\xbf  \n\n", "empty_file"),
    (b"d;a\n", "no_rows"),
    (b'd;a\n"15.01.2026;10\n', "invalid_csv"),
])
def test_corrupted_files(raw, code):
    with pytest.raises(ImportFileError) as raised:
        parse(raw, date_column=0, amount_column=1)
    assert raised.value.code == code


def test_file_size_limit():
    raw = b"d;a\n" + b"15.01.2026;1\n" * (bank_import.MAX_FILE_BYTES // 13 + 1)
    with pytest.raises(ImportFileError) as raised:
        parse(raw, date_column=0, amount_column=1)
    assert raised.value.code == "file_too_large"


def test_row_and_column_limits(monkeypatch):
    monkeypatch.setattr(bank_import, "MAX_ROWS", 3)
    raw = b"d;a\n" + b"15.01.2026;1\n" * 4
    with pytest.raises(ImportFileError) as raised:
        parse(raw, date_column=0, amount_column=1)
    assert raised.value.code == "too_many_rows"

    wide = (";".join(["c"] * (bank_import.MAX_COLUMNS + 1)) + "\n").encode()
    with pytest.raises(ImportFileError) as raised:
        parse(wide + wide)
    assert raised.value.code == "too_many_columns"


def test_long_field_is_rejected():
    raw = csv_bytes(("d", "desc", "a"), ("15.01.2026", "x" * (bank_import.MAX_FIELD_CHARS + 1), "10"))
    assert codes(parse(raw, **SIGNED)) == [(2, "field_too_long")]


def test_categories_defaults_and_formula_injection():
    raw = csv_bytes(
        ("d", "a", "cat"),
        ("15.01.2026", "-100", "=HYPERLINK(\"http://x\")"),
        ("16.01.2026", "-100", ""),
        ("17.01.2026", "200", ""),
        ("18.01.2026", "-100", "c" * 101),
    )
    result = parse(raw, date_column=0, amount_column=1, category_column=2,
                   default_expense_category="Еда", default_income_category="Зарплата")
    assert [r.category for r in result.rows] == ['HYPERLINK("http://x")', "Еда", "Зарплата"]
    assert codes(result) == [(5, "field_too_long")]


def test_hashes_are_stable_and_distinguish_identical_rows():
    raw = csv_bytes(
        ("d", "desc", "a"),
        ("15.01.2026", "Кофе", "-500"),
        ("15.01.2026", "Кофе", "-500"),
        ("15.01.2026", "Чай", "-500"),
    )
    first = [r.import_hash for r in parse(raw, **SIGNED).rows]
    again = [r.import_hash for r in parse(raw, **SIGNED).rows]
    assert first == again
    assert len(set(first)) == 3  # two identical coffees stay two operations
    with_category = [r.import_hash for r in parse(raw, default_expense_category="Еда", **SIGNED).rows]
    assert with_category == first  # category is not part of the identity


def test_unmapped_file_returns_structure_only():
    raw = csv_bytes(("Дата", "Сумма"), ("15.01.2026", "10"), ("16.01.2026", "20"))
    result = parse(raw)
    assert result.rows == [] and result.errors == []
    assert result.total_rows == 2
    assert result.sample == [["15.01.2026", "10"], ["16.01.2026", "20"]]


def test_suggest_mapping_for_supported_languages():
    assert bank_import.suggest_mapping(["Дата", "Описание", "Сумма", "Валюта"]) == {
        "date_column": 0, "description_column": 1, "amount_column": 2, "currency_column": 3}
    assert bank_import.suggest_mapping(["Date", "Description", "Amount"]) == {
        "date_column": 0, "description_column": 1, "amount_column": 2}
    assert bank_import.suggest_mapping(["Күні", "Сипаттама", "Кіріс", "Шығыс"]) == {
        "date_column": 0, "description_column": 1, "income_column": 2, "expense_column": 3,
        "amount_mode": "debit_credit"}


@pytest.mark.parametrize("settings", [
    dict(date_format="YYYY/DD"),
    dict(amount_mode="magic"),
    dict(delimiter="|"),
    dict(decimal_separator=" "),
    dict(amount_mode="type_column", income_values=" , "),
])
def test_invalid_settings(settings):
    with pytest.raises(ImportFileError) as raised:
        parse(b"d;a\n15.01.2026;1\n", **settings)
    assert raised.value.code == "invalid_settings"


# --- Database layer -----------------------------------------------------------


class DatabaseDown(Exception):
    pass


class FakeImportDb:
    """Operations table with a (user_id, import_hash) unique index and real
    commit/rollback semantics."""

    def __init__(self):
        self.rows = []
        self.statements = []
        self.fail_on_insert = None

    def connect(self):
        return FakeImportConnection(self)


class FakeImportConnection:
    def __init__(self, db):
        self.db = db
        self.pending = []
        self.closed = 0

    def cursor(self):
        return FakeImportCursor(self)

    def commit(self):
        self.db.rows.extend(self.pending)
        self.pending = []

    def rollback(self):
        self.pending = []

    def close(self):
        self.closed = 1


class FakeImportCursor:
    def __init__(self, connection):
        self.connection = connection
        self.db = connection.db
        self.result = []

    def __enter__(self): return self
    def __exit__(self, *args): return False

    def execute(self, query, params=()):
        q = " ".join(query.split()).upper()
        self.db.statements.append((q, params))
        if q.startswith("INSERT INTO OPERATIONS (USER_ID, DATE, OPERATION_TYPE, AMOUNT, CATEGORY, IMPORT_HASH)"):
            inserts = sum(1 for statement, _ in self.db.statements if statement.startswith("INSERT"))
            if self.db.fail_on_insert == inserts:
                raise DatabaseDown("connection lost; values (1, '15.01.2026', 'Расход', 100.00, 'Кофе')")
            assert "ON CONFLICT (USER_ID, IMPORT_HASH) WHERE IMPORT_HASH IS NOT NULL DO NOTHING" in q
            existing = {(r["user_id"], r["import_hash"]) for r in self.db.rows + self.connection.pending}
            returned = []
            for start in range(0, len(params), 6):
                user_id, date, operation_type, amount, category, import_hash = params[start:start + 6]
                if (user_id, import_hash) in existing:
                    continue
                existing.add((user_id, import_hash))
                self.connection.pending.append({
                    "id": len(self.db.rows) + len(self.connection.pending) + 1,
                    "user_id": user_id, "date": date, "type": operation_type,
                    "amount": Decimal(str(amount)), "category": category, "import_hash": import_hash,
                })
                returned.append((import_hash,))
            self.result = returned
        elif q.startswith("SELECT IMPORT_HASH FROM OPERATIONS WHERE USER_ID = %S AND IMPORT_HASH = ANY(%S)"):
            user_id, hashes = params
            self.result = [(r["import_hash"],) for r in self.db.rows
                           if r["user_id"] == user_id and r["import_hash"] in hashes]
        elif q.startswith("SELECT ID, DATE, OPERATION_TYPE, AMOUNT, CATEGORY FROM OPERATIONS WHERE USER_ID"):
            self.result = [(r["id"], r["date"], r["type"], r["amount"], r["category"])
                           for r in self.db.rows if r["user_id"] == params[0]]
        else:
            raise AssertionError(f"unexpected SQL: {q}")

    def fetchall(self):
        return list(self.result)

    def fetchone(self):
        return self.result[0] if self.result else None


@pytest.fixture
def db(monkeypatch):
    fake = FakeImportDb()
    monkeypatch.setattr(database, "get_connection", fake.connect)
    return fake


def ops(*rows):
    return [(f"{day:02d}.01.2026, 00:00:00", kind, Decimal(amount), "Другое", f"hash-{day}")
            for day, kind, amount in rows]


def test_import_operations_inserts_and_skips_repeats(db):
    batch = ops((1, "Доход", "100"), (2, "Расход", "40"))
    assert database.import_operations(7, batch) == {"hash-1", "hash-2"}
    assert database.import_operations(7, batch) == set()
    assert len(db.rows) == 2
    assert database.find_imported_hashes(7, ["hash-1", "hash-9"]) == {"hash-1"}


def test_import_is_per_user(db):
    batch = ops((1, "Доход", "100"))
    database.import_operations(1, batch)
    assert database.import_operations(2, batch) == {"hash-1"}
    assert database.find_imported_hashes(2, ["hash-1"]) == {"hash-1"}
    assert sorted(r["user_id"] for r in db.rows) == [1, 2]


def test_large_import_is_split_into_batches(db, monkeypatch):
    monkeypatch.setattr(database, "IMPORT_BATCH_SIZE", 2)
    database.import_operations(1, ops(*[(day, "Расход", "1") for day in range(1, 6)]))
    assert len(db.rows) == 5
    assert sum(1 for q, _ in db.statements if q.startswith("INSERT")) == 3


def test_database_error_rolls_back_whole_import(db, monkeypatch):
    monkeypatch.setattr(database, "IMPORT_BATCH_SIZE", 2)
    db.fail_on_insert = 2  # the second batch fails after the first succeeded
    with pytest.raises(DatabaseDown):
        database.import_operations(1, ops(*[(day, "Расход", "1") for day in range(1, 6)]))
    assert db.rows == []


def test_invalid_operation_is_rejected_before_any_sql(db):
    with pytest.raises(ValueError):
        database.import_operations(1, ops((1, "Расход", "0")))
    assert db.statements == []


def test_find_imported_hashes_without_hashes_skips_query(db):
    assert database.find_imported_hashes(1, []) == set()
    assert db.statements == []


def test_values_are_passed_as_parameters_not_sql(db):
    hostile = "'); DROP TABLE operations; --"
    database.import_operations(1, [("01.01.2026, 00:00:00", "Расход", Decimal("1"), hostile, "h")])
    query, params = db.statements[0]
    assert hostile.upper() not in query
    assert hostile in params


# --- HTTP ---------------------------------------------------------------------


@pytest.fixture
def client(db, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    import api

    currencies = {1: "KZT", 2: "KZT", 3: "USD"}

    def current_user(authorization: str = api.Header(None)):
        if not authorization or not authorization.startswith("Bearer user-"):
            raise api.HTTPException(status_code=401, detail="Требуется авторизация")
        user_id = int(authorization.rsplit("-", 1)[1])
        return {"id": user_id, "email": f"user{user_id}@example.com"}

    monkeypatch.setitem(api.app.dependency_overrides, api.get_current_user, current_user)
    monkeypatch.setattr(database, "get_user_settings", lambda user_id: {"currency": currencies[user_id]})
    api.limiter.reset()
    yield TestClient(api.app)
    api.limiter.reset()


STATEMENT = csv_bytes(
    ("Дата", "Описание", "Сумма", "Валюта"),
    ("15.01.2026", "Зарплата", "350 000,00", "KZT"),
    ("16.01.2026", "Кофейня", "-1 200,50", "KZT"),
    ("16.01.2026", "Кофейня", "-1 200,50", "KZT"),
    ("17.01.2026", "Онлайн-магазин", "-15,99", "USD"),
    ("", "", "", ""),
    ("32.01.2026", "Ошибка", "-10", "KZT"),
    bom=True,
)
MAPPING = "?date_column=0&description_column=1&amount_column=2&currency_column=3&default_expense_category=Еда"
CSV = {"Content-Type": "text/csv"}


def auth(user_id):
    return {**CSV, "Authorization": f"Bearer user-{user_id}"}


def test_full_import_flow(client):
    structure = client.post("/import/csv/preview", content=STATEMENT, headers=auth(1))
    assert structure.status_code == 200
    body = structure.json()
    assert body["mapped"] is False
    assert body["columns"] == ["Дата", "Описание", "Сумма", "Валюта"]
    assert body["suggested"] == {"date_column": 0, "description_column": 1, "amount_column": 2, "currency_column": 3}

    preview = client.post("/import/csv/preview" + MAPPING, content=STATEMENT, headers=auth(1)).json()
    assert (preview["total_rows"], preview["empty_rows"], preview["valid_rows"], preview["error_rows"]) == (5, 1, 3, 2)
    assert preview["duplicate_rows"] == 0
    assert [(r["type"], r["amount"], r["category"]) for r in preview["rows"]] == [
        ("Доход", 350000.0, "Другое"), ("Расход", 1200.5, "Еда"), ("Расход", 1200.5, "Еда")]
    assert [(e["line"], e["code"]) for e in preview["errors"]] == [(5, "currency_mismatch"), (7, "invalid_date")]

    committed = client.post("/import/csv/commit" + MAPPING, content=STATEMENT, headers=auth(1)).json()
    assert (committed["imported"], committed["duplicate_rows"], committed["error_rows"]) == (3, 0, 2)

    operations = client.get("/operations", headers=auth(1)).json()
    assert [(o["date"], o["type"], o["amount"]) for o in operations] == [
        ("15.01.2026, 00:00:00", "Доход", 350000.0),
        ("16.01.2026, 00:00:00", "Расход", 1200.5),
        ("16.01.2026, 00:00:00", "Расход", 1200.5),
    ]
    assert client.get("/balance", headers=auth(1)).json() == {"balance": 347599.0}


def test_repeated_import_creates_no_duplicates(client, db):
    client.post("/import/csv/commit" + MAPPING, content=STATEMENT, headers=auth(1))
    preview = client.post("/import/csv/preview" + MAPPING, content=STATEMENT, headers=auth(1)).json()
    assert preview["duplicate_rows"] == 3
    assert all(row["duplicate"] for row in preview["rows"])

    again = client.post("/import/csv/commit" + MAPPING, content=STATEMENT, headers=auth(1)).json()
    assert (again["imported"], again["duplicate_rows"]) == (0, 3)
    assert len(db.rows) == 3


def test_users_are_isolated(client, db):
    client.post("/import/csv/commit" + MAPPING, content=STATEMENT, headers=auth(1))
    assert client.get("/operations", headers=auth(2)).json() == []

    preview = client.post("/import/csv/preview" + MAPPING, content=STATEMENT, headers=auth(2)).json()
    assert preview["duplicate_rows"] == 0  # user 1's import says nothing about user 2
    second = client.post("/import/csv/commit" + MAPPING, content=STATEMENT, headers=auth(2)).json()
    assert second["imported"] == 3
    assert {r["user_id"] for r in db.rows} == {1, 2}
    assert len(client.get("/operations", headers=auth(1)).json()) == 3


def test_user_currency_decides_which_rows_import(client):
    preview = client.post("/import/csv/preview" + MAPPING, content=STATEMENT, headers=auth(3)).json()
    assert preview["currency"] == "USD"
    assert [r["amount"] for r in preview["rows"]] == [15.99]


def test_import_requires_authentication(client):
    response = client.post("/import/csv/preview", content=STATEMENT, headers=CSV)
    assert response.status_code == 401


def test_rejects_non_csv_content_type(client):
    response = client.post("/import/csv/preview", content=STATEMENT,
                           headers={**auth(1), "Content-Type": "application/json"})
    assert response.status_code == 415
    assert response.json()["detail"] == "Ожидается файл CSV"


def test_rejects_oversized_upload(client):
    big = b"d;a\n" + b"15.01.2026;1\n" * (bank_import.MAX_FILE_BYTES // 13 + 1)
    response = client.post("/import/csv/preview", content=big, headers=auth(1))
    assert response.status_code == 413

    def chunks():  # no Content-Length: the server must still stop reading
        for _ in range(bank_import.MAX_FILE_BYTES // 1000 + 2):
            yield b"x" * 1000

    response = client.post("/import/csv/preview", content=chunks(), headers=auth(1))
    assert response.status_code == 413


@pytest.mark.parametrize("raw,detail", [
    (b"", "Файл пустой"),
    ("Дата;Сумма\n15.01.2026;10\n".encode("cp1251"), "Файл должен быть в кодировке UTF-8"),
    (b"d;a\n", "В файле нет строк с данными"),
])
def test_broken_files_get_clear_errors(client, raw, detail):
    response = client.post("/import/csv/preview", content=raw, headers=auth(1))
    assert response.status_code == 400
    assert response.json()["detail"] == detail


def test_commit_requires_mapping(client, db):
    response = client.post("/import/csv/commit", content=STATEMENT, headers=auth(1))
    assert response.status_code == 422
    assert db.rows == []


def test_invalid_settings_are_rejected(client):
    response = client.post("/import/csv/preview" + MAPPING + "&date_format=nonsense", content=STATEMENT, headers=auth(1))
    assert response.status_code == 422
    response = client.post("/import/csv/preview?date_column=999", content=STATEMENT, headers=auth(1))
    assert response.status_code == 422


def test_database_failure_rolls_back_and_hides_statement_data(client, db, caplog):
    db.fail_on_insert = 1
    with caplog.at_level(logging.DEBUG):
        response = client.post("/import/csv/commit" + MAPPING, content=STATEMENT, headers=auth(1))
    assert response.status_code == 503
    assert response.json()["detail"] == "Не удалось сохранить импорт. Данные не изменены"
    assert db.rows == []
    assert "DatabaseDown" in caplog.text
    for secret in ("Кофейня", "Зарплата", "1200", "350"):
        assert secret not in caplog.text


# --- Regression: de-duplication stability -------------------------------------


def _hashes(raw, currency="KZT", **settings):
    return sorted(r.import_hash for r in parse(raw, currency, **{**SIGNED, **settings}).rows)


def test_hash_does_not_depend_on_profile_currency():
    raw = csv_bytes(("d", "desc", "a"), ("16.01.2026", "Кофе", "-500"))
    assert _hashes(raw, "KZT") == _hashes(raw, "USD")


def test_hash_is_order_independent():
    rows = [("15.01.2026", "Зарплата", "1000"), ("16.01.2026", "Кофе", "-500"),
            ("16.01.2026", "Кофе", "-500"), ("17.01.2026", "Такси", "-900")]
    header = ("d", "desc", "a")
    assert _hashes(csv_bytes(header, *rows)) == _hashes(csv_bytes(header, *reversed(rows)))


def test_reference_column_makes_hash_independent_of_format():
    plain = csv_bytes(("d", "desc", "a", "ref"), ("16.01.2026", "Кофе", "-500", "TX-1"))
    reformatted = csv_bytes(("d", "desc", "a", "ref"), ("16.01.2026 10:42", "COFFEE SHOP #12", "-500", " TX-1 "))
    assert _hashes(plain, reference_column=3) == _hashes(reformatted, reference_column=3)


def test_reference_column_never_merges_distinct_operations():
    raw = csv_bytes(
        ("d", "desc", "a", "ref"),
        ("16.01.2026", "Кофе", "-500", "TX-1"),
        ("16.01.2026", "Кофе", "-500", "TX-2"),   # same purchase twice, different bank IDs
        ("16.01.2026", "Кофе", "-500", "TX-2"),   # bank listed one ID twice: still kept
        ("16.01.2026", "Кофе", "-500", ""),       # no ID: falls back to the row fields
    )
    assert len(set(_hashes(raw, reference_column=3))) == 4


def test_reference_column_is_suggested():
    assert bank_import.suggest_mapping(["Date", "Amount", "Reference"])["reference_column"] == 2


COFFEE_X2 = csv_bytes(("Дата", "Описание", "Сумма"), ("16.01.2026", "Кофе", "-500"), ("16.01.2026", "Кофе", "-500"))
PLAIN = "?date_column=0&description_column=1&amount_column=2"


def test_reordered_statement_imports_nothing_new(client, db):
    rows = [("15.01.2026", "Зарплата", "1000"), ("16.01.2026", "Кофе", "-500"),
            ("16.01.2026", "Кофе", "-500"), ("17.01.2026", "Такси", "-900")]
    header = ("Дата", "Описание", "Сумма")
    first = client.post("/import/csv/commit" + PLAIN, content=csv_bytes(header, *rows), headers=auth(1)).json()
    again = client.post("/import/csv/commit" + PLAIN, content=csv_bytes(header, *reversed(rows)), headers=auth(1)).json()
    assert (first["imported"], again["imported"], again["duplicate_rows"]) == (4, 0, 4)
    assert len(db.rows) == 4


def test_two_real_identical_purchases_are_both_kept(client, db):
    first = client.post("/import/csv/commit" + PLAIN, content=COFFEE_X2, headers=auth(1)).json()
    assert first["imported"] == 2
    assert client.post("/import/csv/commit" + PLAIN, content=COFFEE_X2, headers=auth(1)).json()["imported"] == 0
    # A later statement shows a third identical coffee that day: only it is new.
    three = csv_bytes(("Дата", "Описание", "Сумма"), *[("16.01.2026", "Кофе", "-500")] * 3)
    assert client.post("/import/csv/commit" + PLAIN, content=three, headers=auth(1)).json()["imported"] == 1
    assert len(db.rows) == 3


def test_overlapping_statements_import_only_new_rows(client, db):
    header = ("Дата", "Описание", "Сумма")
    january = csv_bytes(header, *[(f"{d}.01.2026", "Покупка", f"-{d}") for d in range(10, 21)])
    late_january = csv_bytes(header, *[(f"{d}.01.2026", "Покупка", f"-{d}") for d in range(15, 26)])
    client.post("/import/csv/commit" + PLAIN, content=january, headers=auth(1))
    second = client.post("/import/csv/commit" + PLAIN, content=late_january, headers=auth(1)).json()
    assert (second["imported"], second["duplicate_rows"]) == (5, 6)
    assert len(db.rows) == 16


# --- Regression: API and security ---------------------------------------------


@pytest.mark.parametrize("content_type,status", [
    ("text/csv; charset=utf-8", 200),
    ("TEXT/CSV", 200),
    ("text/plain", 415),
    ("multipart/form-data; boundary=x", 415),
    ("application/octet-stream", 415),
])
def test_content_type_validation(client, content_type, status):
    response = client.post("/import/csv/preview", content=STATEMENT,
                           headers={**auth(1), "Content-Type": content_type})
    assert response.status_code == status


def test_missing_content_type_is_rejected(client):
    response = client.post("/import/csv/preview", content=STATEMENT, headers={"Authorization": "Bearer user-1"})
    assert response.status_code == 415


@pytest.mark.parametrize("query", [
    "?date_column=-1&amount_column=1",
    "?date_column=0&amount_column=1&delimiter=%7C",
    "?date_column=0&amount_column=1&income_values=" + "x" * 201,
    "?date_column=0&amount_column=1&default_expense_category=" + "x" * 101,
    "?date_column=0&amount_column=1&reference_column=50",
    "?date_column=0&amount_column=1&has_header=maybe",
])
def test_invalid_import_parameters(client, query):
    response = client.post("/import/csv/preview" + query, content=STATEMENT, headers=auth(1))
    assert response.status_code == 422


def test_row_errors_never_cost_valid_rows(client, db):
    raw = csv_bytes(
        ("Дата", "Описание", "Сумма", "Валюта"),
        ("15.01.2026", "ok-1", "100", "KZT"),
        ("31.02.2026", "bad date", "100", "KZT"),
        ("15.01.2026", "bad amount", "abc", "KZT"),
        ("15.01.2026", "zero", "0", "KZT"),
        ("15.01.2026", "ambiguous", "1,234", "KZT"),
        ("15.01.2026", "too big", "9 999 999 999 999", "KZT"),
        ("15.01.2026", "currency", "100", "EUR"),
        ("15.01.2026", "x" * 501, "100", "KZT"),
        ("15.01.2026", "short row"),
        ("16.01.2026", "ok-2", "-50", "KZT"),
    )
    preview = client.post("/import/csv/preview" + MAPPING, content=raw, headers=auth(1)).json()
    committed = client.post("/import/csv/commit" + MAPPING, content=raw, headers=auth(1)).json()
    assert committed["imported"] == preview["valid_rows"] == 2
    assert committed["error_rows"] == preview["error_rows"] == 8
    assert sorted(r["amount"] for r in db.rows) == [Decimal("50.00"), Decimal("100.00")]


@pytest.fixture
def real_auth_client(db, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    import api

    users = {1: (1, "user1@example.com", "hash", None, 0), 2: (2, "user2@example.com", "hash", None, 3)}
    monkeypatch.setattr(database, "get_user_by_id", lambda user_id: users.get(user_id))
    monkeypatch.setattr(database, "get_user_settings", lambda user_id: {"currency": "KZT"})
    api.app.dependency_overrides.pop(api.get_current_user, None)
    api.limiter.reset()
    yield TestClient(api.app)
    api.limiter.reset()


def test_real_token_auth_and_user_binding(real_auth_client, db):
    import auth as auth_module

    token = auth_module.create_access_token({"user_id": 1, "tv": 0})
    stale = auth_module.create_access_token({"user_id": 2, "tv": 2})  # password changed since
    for headers in ({}, {"Authorization": "Bearer nonsense"}, {"Authorization": f"Bearer {stale}"}):
        for path in ("/import/csv/preview", "/import/csv/commit" + MAPPING):
            response = real_auth_client.post(path, content=STATEMENT, headers={**CSV, **headers})
            assert response.status_code == 401
    assert db.rows == []

    # A user id in the query string is ignored: the token decides the owner.
    response = real_auth_client.post("/import/csv/commit" + MAPPING + "&user_id=2", content=STATEMENT,
                                     headers={**CSV, "Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert {r["user_id"] for r in db.rows} == {1}


def test_import_rate_limit_is_per_user(real_auth_client):
    import auth as auth_module

    tokens = {1: auth_module.create_access_token({"user_id": 1, "tv": 0}),
              2: auth_module.create_access_token({"user_id": 2, "tv": 3})}
    small = csv_bytes(("Дата", "Сумма"), ("15.01.2026", "10"))

    def commit(user_id):
        return real_auth_client.post("/import/csv/commit?date_column=0&amount_column=1", content=small,
                                     headers={**CSV, "Authorization": f"Bearer {tokens[user_id]}"}).status_code

    statuses = [commit(1) for _ in range(11)]
    assert statuses[:10] == [200] * 10 and statuses[10] == 429
    # Same client address, different user: not affected by user 1's limit.
    assert commit(2) == 200
