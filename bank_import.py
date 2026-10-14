"""Bank statement CSV parsing for the import feature.

Pure functions only: no database access, no logging of statement contents.
Row-level problems are reported as error codes so the frontend can localize them.
"""
import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import List, Optional

MAX_FILE_BYTES = 1_000_000
MAX_ROWS = 5000
MAX_COLUMNS = 50
MAX_FIELD_CHARS = 500
MAX_CATEGORY_CHARS = 100
MAX_AMOUNT = Decimal("999999999999.99")  # NUMERIC(14, 2)
STORED_DATE_FORMAT = "%d.%m.%Y, %H:%M:%S"  # the format the frontend saves
DEFAULT_CATEGORY = "Другое"
INCOME, EXPENSE = "Доход", "Расход"

_TIME_SUFFIXES = ("", " %H:%M", " %H:%M:%S", ", %H:%M", ", %H:%M:%S", "T%H:%M", "T%H:%M:%S")
DATE_FORMATS = {
    "DD.MM.YYYY": "%d.%m.%Y",
    "DD/MM/YYYY": "%d/%m/%Y",
    "MM/DD/YYYY": "%m/%d/%Y",
    "DD-MM-YYYY": "%d-%m-%Y",
    "YYYY-MM-DD": "%Y-%m-%d",
}
AMOUNT_MODES = ("signed", "type_column", "debit_credit")
CURRENCY_ALIASES = {
    "KZT": "KZT", "₸": "KZT", "ТГ": "KZT", "ТЕНГЕ": "KZT",
    "USD": "USD", "$": "USD",
    "EUR": "EUR", "€": "EUR",
    "RUB": "RUB", "₽": "RUB", "РУБ": "RUB",
}

# Header keywords used to pre-fill the column mapping. A bank-specific preset
# can be added here later with its own aliases and default settings.
BANK_PRESETS = {
    "generic": {
        "aliases": {
            "date_column": ("дата", "date", "күні", "күн", "дата операции", "transaction date"),
            "amount_column": ("сумма", "amount", "сома", "сумма операции", "sum"),
            "description_column": ("описание", "description", "сипаттама", "назначение", "details", "merchant"),
            "category_column": ("категория", "category", "санат"),
            "currency_column": ("валюта", "currency", "валютасы"),
            "reference_column": ("номер операции", "референс", "reference", "transaction id", "id операции", "операция нөмірі"),
            "type_column": ("тип", "type", "түрі", "направление"),
            "income_column": ("приход", "поступление", "credit", "кіріс", "зачисление"),
            "expense_column": ("расход", "списание", "debit", "шығыс"),
        },
        "settings": {},
    },
}


class ImportFileError(ValueError):
    """The file as a whole cannot be imported. ``code`` is stable for the frontend."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass
class ImportSettings:
    delimiter: str = "auto"
    has_header: bool = True
    date_column: Optional[int] = None
    date_format: str = "DD.MM.YYYY"
    amount_mode: str = "signed"
    amount_column: Optional[int] = None
    invert_sign: bool = False
    type_column: Optional[int] = None
    income_values: str = ""
    expense_values: str = ""
    income_column: Optional[int] = None
    expense_column: Optional[int] = None
    description_column: Optional[int] = None
    category_column: Optional[int] = None
    currency_column: Optional[int] = None
    reference_column: Optional[int] = None
    decimal_separator: str = "auto"
    default_income_category: str = DEFAULT_CATEGORY
    default_expense_category: str = DEFAULT_CATEGORY

    def is_mapped(self):
        if self.date_column is None:
            return False
        if self.amount_mode == "debit_credit":
            return self.income_column is not None and self.expense_column is not None
        if self.amount_mode == "type_column":
            return self.amount_column is not None and self.type_column is not None
        return self.amount_column is not None


@dataclass
class ParsedRow:
    line: int
    date: str
    operation_type: str
    amount: Decimal
    category: str
    description: str
    import_hash: str


@dataclass
class ParseResult:
    columns: List[str]
    sample: List[List[str]]
    delimiter: str
    total_rows: int = 0
    empty_rows: int = 0
    rows: List[ParsedRow] = field(default_factory=list)
    errors: List[dict] = field(default_factory=list)


class _RowError(Exception):
    def __init__(self, code, column=None):
        super().__init__(code)
        self.code = code
        self.column = column


def decode_csv(raw: bytes) -> str:
    if len(raw) > MAX_FILE_BYTES:
        raise ImportFileError("file_too_large", "Файл слишком большой")
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImportFileError("invalid_encoding", "Файл должен быть в кодировке UTF-8") from exc
    if "\x00" in text:
        raise ImportFileError("invalid_content", "Файл не похож на CSV")
    if not text.strip():
        raise ImportFileError("empty_file", "Файл пустой")
    return text


def _read_rows(text, delimiter):
    try:
        return list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True))
    except csv.Error as exc:
        raise ImportFileError("invalid_csv", "Не удалось прочитать CSV") from exc


def detect_delimiter(text):
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    counts = {d: len(_read_rows(first_line, d)[0]) if first_line else 0 for d in (",", ";")}
    return ";" if counts[";"] > counts[","] else ","


def suggest_mapping(columns, preset="generic"):
    aliases = BANK_PRESETS.get(preset, BANK_PRESETS["generic"])["aliases"]
    normalized = [column.strip().lower() for column in columns]
    mapping = {}
    for setting, names in aliases.items():
        for index, column in enumerate(normalized):
            if column in names and index not in mapping.values():
                mapping[setting] = index
                break
    if "amount_column" not in mapping and {"income_column", "expense_column"} <= mapping.keys():
        mapping["amount_mode"] = "debit_credit"
    return mapping


def _cell(fields, index, required=True):
    if index is None:
        return ""
    if index >= len(fields):
        if required:
            raise _RowError("missing_value", index)
        return ""
    value = fields[index].strip()
    if len(value) > MAX_FIELD_CHARS:
        raise _RowError("field_too_long", index)
    if required and not value:
        raise _RowError("missing_value", index)
    return value


def parse_date(value, date_format):
    base = DATE_FORMATS[date_format]
    for suffix in _TIME_SUFFIXES:
        try:
            parsed = datetime.strptime(value, base + suffix)
        except ValueError:
            continue
        if 1970 <= parsed.year <= 2100:
            return parsed.strftime(STORED_DATE_FORMAT)
    raise _RowError("invalid_date")


_GROUPED = {
    ".": re.compile(r"^(\d{1,3}(,\d{3})+|\d+)(\.\d+)?$"),
    ",": re.compile(r"^(\d{1,3}(\.\d{3})+|\d+)(,\d+)?$"),
}
_AMBIGUOUS = re.compile(r"^\d{1,3}[.,]\d{3}$")
_CURRENCY_EDGES = re.compile(r"^[^\d(+\-−]+|[^\d)\-−]+$")


def parse_amount(value, decimal_separator="auto"):
    """Returns a signed Decimal. Accepts '1 234,56', '-1,234.56', '(500)', '500-', '₸ 1 000'."""
    text = value.replace(" ", "").replace(" ", "").replace(" ", "").replace("'", "")
    text = _CURRENCY_EDGES.sub("", text).replace("−", "-")
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    if text.endswith("-"):
        negative, text = not negative, text[:-1]
    if text and text[0] in "+-":
        negative, text = (text[0] == "-") != negative, text[1:]
    if not text:
        raise _RowError("invalid_amount")
    if decimal_separator not in _GROUPED and _AMBIGUOUS.match(text):
        # '1,234' is 1.234 or 1234 depending on the bank: make the user choose.
        raise _RowError("ambiguous_number")
    separators = (decimal_separator,) if decimal_separator in _GROUPED else (",", ".")
    for separator in separators:
        if _GROUPED[separator].match(text):
            grouping = "," if separator == "." else "."
            number = text.replace(grouping, "").replace(separator, ".")
            break
    else:
        raise _RowError("invalid_amount")
    try:
        amount = Decimal(number).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise _RowError("invalid_amount") from exc
    if amount > MAX_AMOUNT:
        raise _RowError("amount_too_large")
    return -amount if negative else amount


def _value_set(values):
    return {item.strip().lower() for item in values.split(",") if item.strip()}


def _clean_text(value):
    # Neutralise spreadsheet formulas (CSV injection) before the value is stored
    # and later exported to CSV/Excel.
    return value.lstrip("=+-@\t\r ").strip()


def _resolve_amount(fields, settings):
    if settings.amount_mode == "debit_credit":
        credit = _cell(fields, settings.income_column, required=False)
        debit = _cell(fields, settings.expense_column, required=False)
        credit_amount = abs(parse_amount(credit, settings.decimal_separator)) if credit else Decimal(0)
        debit_amount = abs(parse_amount(debit, settings.decimal_separator)) if debit else Decimal(0)
        if credit_amount and debit_amount:
            raise _RowError("ambiguous_amount")
        if not credit_amount and not debit_amount:
            raise _RowError("zero_amount")
        return (INCOME, credit_amount) if credit_amount else (EXPENSE, debit_amount)

    amount = parse_amount(_cell(fields, settings.amount_column), settings.decimal_separator)
    if amount == 0:
        raise _RowError("zero_amount")
    if settings.amount_mode == "type_column":
        marker = _cell(fields, settings.type_column).lower()
        if marker in _value_set(settings.income_values):
            return INCOME, abs(amount)
        if marker in _value_set(settings.expense_values):
            return EXPENSE, abs(amount)
        raise _RowError("unknown_type", settings.type_column)

    is_income = amount > 0
    if settings.invert_sign:
        is_income = not is_income
    return (INCOME if is_income else EXPENSE), abs(amount)


def _resolve_currency(fields, settings, user_currency):
    raw = _cell(fields, settings.currency_column, required=False)
    if not raw:
        return user_currency
    currency = CURRENCY_ALIASES.get(raw.upper())
    if currency != user_currency:
        raise _RowError("currency_mismatch", settings.currency_column)
    return currency


def _in_column(column, parse, *args):
    try:
        return parse(*args)
    except _RowError as error:
        if error.column is None:
            error.column = column
        raise


def _parse_row(fields, settings, user_currency):
    date = _in_column(settings.date_column, parse_date, _cell(fields, settings.date_column), settings.date_format)
    amount_column = settings.income_column if settings.amount_mode == "debit_credit" else settings.amount_column
    operation_type, amount = _in_column(amount_column, _resolve_amount, fields, settings)
    _resolve_currency(fields, settings, user_currency)  # rejects rows in another currency
    description = _cell(fields, settings.description_column, required=False)
    reference = _cell(fields, settings.reference_column, required=False)
    category = _clean_text(_cell(fields, settings.category_column, required=False))
    if not category:
        default = settings.default_income_category if operation_type == INCOME else settings.default_expense_category
        category = _clean_text(default) or DEFAULT_CATEGORY
    if len(category) > MAX_CATEGORY_CHARS:
        raise _RowError("field_too_long", settings.category_column)
    return date, operation_type, amount, category, description, reference


def _identity(date, operation_type, amount, description, reference):
    """What makes two statement rows the same bank operation.

    A bank reference number identifies the operation on its own, so the
    fingerprint survives changes in description, date format or mapping.
    Without one, date, type, amount and description are used. Currency is
    left out: only rows in the profile currency are imported anyway, so it
    adds nothing but would change every fingerprint if the profile currency
    changed. Identical rows are told apart by their occurrence number (see
    parse_statement), so two real identical purchases are never merged.
    """
    if reference:
        return ("ref", " ".join(reference.split()))
    return ("row", date, operation_type, f"{amount:.2f}", " ".join(description.lower().split()))


def _import_hash(identity, occurrence):
    key = "|".join(("v1", *identity, str(occurrence)))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def validate_settings(settings):
    if settings.delimiter not in ("auto", ",", ";"):
        raise ImportFileError("invalid_settings", "Недопустимые настройки импорта")
    if settings.date_format not in DATE_FORMATS or settings.amount_mode not in AMOUNT_MODES:
        raise ImportFileError("invalid_settings", "Недопустимые настройки импорта")
    if settings.decimal_separator not in ("auto", ",", "."):
        raise ImportFileError("invalid_settings", "Недопустимые настройки импорта")
    if settings.amount_mode == "type_column" and not (
        _value_set(settings.income_values) or _value_set(settings.expense_values)
    ):
        raise ImportFileError("invalid_settings", "Укажите значения типа для доходов и расходов")


def parse_statement(raw: bytes, settings: ImportSettings, user_currency: str) -> ParseResult:
    validate_settings(settings)
    text = decode_csv(raw)
    delimiter = detect_delimiter(text) if settings.delimiter == "auto" else settings.delimiter
    table = _read_rows(text, delimiter)
    width = max((len(row) for row in table), default=0)
    if width > MAX_COLUMNS:
        raise ImportFileError("too_many_columns", "Слишком много столбцов")

    start = 0
    if settings.has_header:
        start = next((i for i, row in enumerate(table) if any(cell.strip() for cell in row)), len(table))
        columns = [cell.strip()[:MAX_FIELD_CHARS] for cell in table[start]] if start < len(table) else []
        start += 1
    else:
        columns = []
    columns += [""] * (width - len(columns))
    data = table[start:]
    if sum(1 for row in data if any(cell.strip() for cell in row)) > MAX_ROWS:
        raise ImportFileError("too_many_rows", "Слишком много строк")

    result = ParseResult(columns=columns, sample=[], delimiter=delimiter)
    occurrences = {}
    for offset, fields in enumerate(data):
        line = start + offset + 1  # 1-based line in the file
        if not any(cell.strip() for cell in fields):
            result.empty_rows += 1
            continue
        result.total_rows += 1
        if len(result.sample) < 5:
            result.sample.append([cell.strip()[:100] for cell in fields])
        if not settings.is_mapped():
            continue
        try:
            date, operation_type, amount, category, description, reference = _parse_row(fields, settings, user_currency)
        except _RowError as error:
            result.errors.append({"line": line, "code": error.code, "column": error.column})
            continue
        identity = _identity(date, operation_type, amount, description, reference)
        occurrences[identity] = occurrences.get(identity, 0) + 1
        result.rows.append(ParsedRow(
            line=line,
            date=date,
            operation_type=operation_type,
            amount=amount,
            category=category,
            description=description,
            import_hash=_import_hash(identity, occurrences[identity]),
        ))
    if result.total_rows == 0:
        raise ImportFileError("no_rows", "В файле нет строк с данными")
    return result
