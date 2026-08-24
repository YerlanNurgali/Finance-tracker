from main import parse_operation


def test_income():
    operation = "Доход: +15000 тенге"

    result = parse_operation(operation)

    assert result == ("Доход", 15000.0, "")


def test_expense_with_category():
    operation = "24.08.2026 00:42 | Расход: -3500 тенге | Категория: Развлечения"

    result = parse_operation(operation)

    assert result == ("Расход", 3500.0, "Развлечения")

from main import calculate_balance


def test_calculate_balance():
    operations = [
        "24.08.2026 10:00 | Доход: +15000 тенге",
        "24.08.2026 11:00 | Расход: -2000 тенге | Категория: Еда",
        "24.08.2026 12:00 | Расход: -1500 тенге | Категория: Транспорт",
    ]

    result = calculate_balance(operations)

    assert result == 11500.0

from main import get_category_expenses


def test_category_expenses():
    operations = [
        "24.08.2026 10:00 | Расход: -1000 тенге | Категория: Еда",
        "24.08.2026 11:00 | Расход: -2000 тенге | Категория: Транспорт",
        "24.08.2026 12:00 | Расход: -500 тенге | Категория: Еда",
    ]

    result = get_category_expenses(operations)

    assert result == {
        "Еда": 1500.0,
        "Транспорт": 2000.0,
    }

from unittest.mock import patch
from main import edit_operation


def test_edit_operation():
    operations = [
        "24.08.2026 00:42 | Расход: -1500 тенге | Категория: Транспорт"
    ]

    with patch("builtins.input", side_effect=["1", "2000", "4"]):
        edit_operation(operations)

    assert operations[0] == (
        "24.08.2026 00:42 | Расход: -2 000 тенге | Категория: Развлечения"
    )

from unittest.mock import patch
from main import delete_operation


def test_delete_operation():
    operations = [
        "Доход: +10000 тенге",
        "Расход: -2000 тенге",
        "Доход: +5000 тенге",
    ]

    with patch("builtins.input", return_value="2"):
        delete_operation(operations)

    assert operations == [
        "Доход: +10000 тенге",
        "Доход: +5000 тенге",
    ]

print("Все тесты пройдены!")
