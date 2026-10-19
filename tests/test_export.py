"""Exported files are opened in spreadsheet software, so user text in them must
stay text. No database is used: the data layer is replaced with test rows."""
import csv
import io
import types
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

    @property
    def max_row(self):
        return len(self.rows)

    def cell(self, row, column):
        return types.SimpleNamespace(number_format=None)


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


# --- Excel: a real workbook ---------------------------------------------------------

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _workbook(response):
    openpyxl = pytest.importorskip("openpyxl")
    return openpyxl.load_workbook(io.BytesIO(response.content)).active


def test_excel_export_is_a_workbook_with_the_operations(client):
    pytest.importorskip("openpyxl")
    response = client.get("/export/excel?period=all")
    assert response.status_code == 200
    assert response.headers["content-type"] == XLSX_TYPE
    assert response.headers["content-disposition"] == "attachment; filename=finance_export_all.xlsx"
    sheet = _workbook(response)
    rows = [[cell.value for cell in row] for row in sheet.iter_rows()]
    assert sheet.title == "Операции"
    assert rows[0] == ["ID", "Дата", "Тип", "Сумма", "Категория"]
    assert [row[1] for row in rows[1:]] == [row[1] for row in ROWS]
    assert [row[2] for row in rows[1:]] == [row[2] for row in ROWS]
    assert [row[3] for row in rows[1:]] == [10.0, 5.5, 1.0, 1.0, 1000.0, 7.0]
    assert [row[4] for row in rows[1:]] == [
        '\'=HYPERLINK("http://evil.test","x")', "'+SUM(1,2)", "'-2+3", "'@cmd", "Зарплата", None,
    ]
    for row in sheet.iter_rows(min_row=2):
        assert row[3].data_type == "n" and row[3].number_format == "0.00", "amounts stay numbers"
        assert all(cell.data_type != "f" for cell in row), "nothing is stored as a formula"


def test_excel_export_survives_control_characters(client, monkeypatch):
    pytest.importorskip("openpyxl")
    monkeypatch.setattr(database, "get_operations", lambda user_id: [
        (1, "16.10.2026", "Расход", Decimal("999999999999.99"), "bell\x07 tab\t ok"),
    ])
    response = client.get("/export/excel")
    assert response.status_code == 200
    row = [cell.value for cell in list(_workbook(response).iter_rows())[1]]
    assert row[3] == 999999999999.99
    assert row[4] == "bell? tab\t ok"


def test_empty_excel_export_has_only_the_header(client, monkeypatch):
    pytest.importorskip("openpyxl")
    monkeypatch.setattr(database, "get_operations", lambda user_id: [])
    rows = list(_workbook(client.get("/export/excel?period=today")).iter_rows(values_only=True))
    assert rows == [("ID", "Дата", "Тип", "Сумма", "Категория")]


# --- PDF ---------------------------------------------------------------------------

KAZAKH = "Азық-түлік: ӘҒҚҢӨҰҮҺІ әғқңөұүһі"
LONG_EMAIL = "very.long.address.for.the.pdf.layout.check.qazaqstan@subdomain.example-company.test"


@pytest.fixture
def pdf(monkeypatch):
    """Records what the export draws on a real reportlab canvas."""
    pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas
    if api.pdf_fonts() is None:
        pytest.skip("no font with Russian and Kazakh letters on this machine (set PDF_FONT_PATH)")
    drawn = {"pages": 1, "text": [], "fonts": set()}
    original = {name: getattr(canvas.Canvas, name) for name in ("drawString", "drawRightString", "showPage", "setFont")}

    def record(name):
        def method(self, *args, **kwargs):
            if name == "showPage":
                drawn["pages"] += 1
            elif name == "setFont":
                drawn["fonts"].add(args[0])
            else:
                x, y, text = args[:3]
                drawn["text"].append({
                    "page": drawn["pages"], "x": x, "y": y, "text": text, "font": self._fontname,
                    "right": name == "drawRightString", "size": self._fontsize,
                })
            return original[name](self, *args, **kwargs)
        return method

    for name in original:
        monkeypatch.setattr(canvas.Canvas, name, record(name))
    return drawn


def _pdf_rows(count):
    categories = ["Жалақы", KAZAKH, "Очень длинная категория с пробелами, которая переносится на несколько строк",
                  "Сверхдлиннаякатегориябезпробеловкотораяразбиваетсяпосимволамчтобынепереполнитьколонку",
                  None, "=HYPERLINK(1)"]
    return [(index + 1, f"{index % 28 + 1:02d}.10.2026, 10:00:00", "Доход" if index % 3 == 0 else "Расход",
             Decimal("999999999999.99") if index == 1 else Decimal(index) * Decimal("1234.50"),
             categories[index % len(categories)]) for index in range(count)]


def _export_pdf(monkeypatch, rows, email="user@example.test"):
    monkeypatch.setattr(api.app.router, "on_startup", [])
    monkeypatch.setitem(api.app.dependency_overrides, api.get_current_user, lambda: {"id": 1, "email": email})
    monkeypatch.setattr(database, "get_operations", lambda user_id: rows)
    return TestClient(api.app).get("/export/pdf?period=all")


def test_pdf_export_is_a_valid_pdf(pdf, monkeypatch):
    response = _export_pdf(monkeypatch, _pdf_rows(3))
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == "attachment; filename=finance_export_all.pdf"
    assert response.content.startswith(b"%PDF-") and response.content.rstrip().endswith(b"%%EOF")
    assert b"/FontFile2" in response.content, "the Unicode font is embedded"
    assert b"Helvetica" not in response.content


def test_pdf_text_is_copyable(pdf, monkeypatch):
    # Every embedded font carries a ToUnicode map, so viewers can copy and search the text.
    content = _export_pdf(monkeypatch, _pdf_rows(3)).content
    assert content.count(b"/ToUnicode") == content.count(b"/FontFile2") >= 1


def test_pdf_draws_russian_and_kazakh_text_in_the_unicode_font(pdf, monkeypatch):
    _export_pdf(monkeypatch, _pdf_rows(3), email="пользователь@пример.kz")
    text = " ".join(item["text"] for item in pdf["text"])
    for expected in ("Отчет", "Пользователь: пользователь@пример.kz", "Дата", "Тип", "Сумма", "Категория",
                     "Доход", "Расход", "Жалақы", KAZAKH):
        assert expected in text
    assert pdf["fonts"] <= {api.PDF_FONT, api.PDF_BOLD_FONT}, "never the standard fonts without Cyrillic"
    assert all(item["font"] in (api.PDF_FONT, api.PDF_BOLD_FONT) for item in pdf["text"])


def test_pdf_amounts_keep_their_format_and_currency(pdf, monkeypatch):
    _export_pdf(monkeypatch, _pdf_rows(3))
    amounts = [item["text"] for item in pdf["text"] if item["right"] and item["text"] != "Сумма"]
    currency = "₸" if ord("₸") in api.pdf_fonts()[2] else "KZT"
    assert amounts == [f"0.00 {currency}", f"999999999999.99 {currency}", f"2469.00 {currency}"]


def test_pdf_long_values_stay_inside_their_columns_and_lose_nothing(pdf, monkeypatch):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfbase.pdfmetrics import stringWidth
    rows = _pdf_rows(6)
    _export_pdf(monkeypatch, rows, email=LONG_EMAIL)
    page_width = letter[0]
    for item in pdf["text"]:
        width = stringWidth(item["text"], item["font"], item["size"])
        left = item["x"] - width if item["right"] else item["x"]
        assert left >= api.PDF_MARGIN - 0.01 and left + width <= page_width - api.PDF_MARGIN + 0.01, item
    drawn = "".join(item["text"].replace(" ", "") for item in pdf["text"])
    assert LONG_EMAIL in drawn
    for row in rows:
        assert (row[4] or "-").replace(" ", "") in drawn


def test_pdf_repeats_the_table_header_on_every_page_without_overlaps(pdf, monkeypatch):
    rows = _pdf_rows(120)
    _export_pdf(monkeypatch, rows)
    pages = sorted({item["page"] for item in pdf["text"]})
    assert pages == list(range(1, len(pages) + 1)) and len(pages) >= 3
    for page in pages:
        on_page = [item for item in pdf["text"] if item["page"] == page]
        headings = [item["text"] for item in on_page if item["font"] == api.PDF_BOLD_FONT or
                    (api.pdf_fonts()[1] == api.PDF_FONT and item["text"] in ("Дата", "Тип", "Сумма", "Категория"))]
        assert {"Дата", "Тип", "Сумма", "Категория"} <= set(headings), f"page {page} has the table header"
        assert all(item["y"] >= api.PDF_MARGIN for item in on_page), "nothing runs off the bottom"
        for column_x in {item["x"] for item in on_page}:
            ys = sorted(item["y"] for item in on_page if item["x"] == column_x)
            assert all(b - a >= api.PDF_LEADING - 0.01 for a, b in zip(ys, ys[1:])), "lines never overlap"
    import re
    dates = [item["text"] for item in pdf["text"] if re.fullmatch(r"\d\d\.10\.2026, 10:00:00", item["text"])]
    assert dates == [row[1] for row in rows], "every row is drawn once, in order, across the page breaks"


def test_empty_pdf_export_says_so(pdf, monkeypatch):
    response = _export_pdf(monkeypatch, [])
    assert response.status_code == 200 and response.content.startswith(b"%PDF-")
    assert "Нет операций за выбранный период" in [item["text"] for item in pdf["text"]]


def test_pdf_replaces_characters_the_font_cannot_draw(pdf, monkeypatch):
    _export_pdf(monkeypatch, [(1, "16.10.2026", "Расход", Decimal("1.00"), "bell\x07 \U0001F389 Жалақы")])
    assert "bell? ? Жалақы" in [item["text"] for item in pdf["text"]]


@pytest.mark.parametrize("text,width", [
    ("Очень длинная категория с пробелами, которая переносится на несколько строк", 120),
    ("Сверхдлиннаякатегориябезпробеловкотораяразбиваетсяпосимволам", 80),
    ("", 50),
])
def test_pdf_wrap_keeps_every_character_within_the_width(text, width):
    pytest.importorskip("reportlab")
    from reportlab.pdfbase.pdfmetrics import stringWidth
    lines = api._pdf_wrap(text, "Helvetica", 10, width)
    assert all(stringWidth(line, "Helvetica", 10) <= width for line in lines)
    assert "".join(lines).replace(" ", "") == text.replace(" ", "")


# --- Errors, authorisation and isolation for every format ---------------------------

def test_pdf_without_a_suitable_font_is_a_503_without_details(monkeypatch, caplog):
    pytest.importorskip("reportlab")
    monkeypatch.setattr(api, "pdf_fonts", lambda: None)
    response = _export_pdf(monkeypatch, _pdf_rows(2))
    assert response.status_code == 503
    assert response.json() == {"detail": "Экспорт в PDF временно недоступен"}
    assert "PDF_FONT_PATH" in caplog.text and "/System" not in response.text


def test_a_failure_while_building_a_file_is_a_plain_500(monkeypatch):
    pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas
    monkeypatch.setattr(canvas.Canvas, "save", lambda self: (_ for _ in ()).throw(RuntimeError("/secret/path font error")))
    monkeypatch.setattr(api.app.router, "on_startup", [])
    monkeypatch.setitem(api.app.dependency_overrides, api.get_current_user, lambda: {"id": 1, "email": "u@example.test"})
    monkeypatch.setattr(database, "get_operations", lambda user_id: _pdf_rows(1))
    response = TestClient(api.app, raise_server_exceptions=False).get("/export/pdf")
    if api.pdf_fonts() is None:
        assert response.status_code == 503
    else:
        assert response.status_code == 500 and response.text == "Internal Server Error"
    assert "/secret/path" not in response.text


@pytest.mark.parametrize("fmt", ["csv", "excel", "pdf"])
def test_every_export_needs_a_valid_token(monkeypatch, fmt):
    monkeypatch.setattr(api.app.router, "on_startup", [])
    plain = TestClient(api.app)
    assert plain.get(f"/export/{fmt}").status_code == 401
    assert plain.get(f"/export/{fmt}", headers={"Authorization": "Bearer forged.token"}).status_code == 401


@pytest.mark.parametrize("fmt", ["csv", "excel", "pdf"])
def test_every_export_reads_only_the_signed_in_users_operations(monkeypatch, fmt):
    pytest.importorskip("openpyxl")
    pytest.importorskip("reportlab")
    if fmt == "pdf" and api.pdf_fonts() is None:
        pytest.skip("no PDF font on this machine")
    asked = []
    monkeypatch.setattr(api.app.router, "on_startup", [])
    monkeypatch.setitem(api.app.dependency_overrides, api.get_current_user, lambda: {"id": 42, "email": "b@example.test"})
    monkeypatch.setattr(database, "get_operations", lambda user_id: asked.append(user_id) or [])
    assert TestClient(api.app).get(f"/export/{fmt}?period=month").status_code == 200
    assert asked == [42]


@pytest.mark.parametrize("fmt", ["excel", "pdf"])
def test_export_period_is_validated_for_every_format(client, fmt):
    assert client.get(f"/export/{fmt}?period=year").status_code == 422
